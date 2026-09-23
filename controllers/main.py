"""Read-only public JSON API consumed by the external (non-Odoo) website.

Endpoint set mirrors the content types managed by admin-web.snapp.am:

    admin-web form            this API
    ----------------------    -------------------------------
    home-banner               /api/v1/banners?page=home
    about-us-banner           /api/v1/banners?page=about_us
    home-faq                  /api/v1/faq
    news                      /api/v1/news, /api/v1/news/<id>
    careers                   /api/v1/jobs, /api/v1/jobs/<id>

The API is open: no key, no token, callable from a Next.js server component or
straight from the browser. An optional key can be switched on later without a
code change -- see `api_key_optional` and `dg.api.client`.

Design rules for anything added here:

* Never expose a generic ``/api/<model>`` passthrough. Each endpoint hand-picks
  the fields it returns, so adding a field to a model can never silently start
  publishing it to the internet. This matters more, not less, without a key.
* Never call ``sudo()`` unless there is no alternative. Requests execute as the
  public user, so the record rules restrict every read to published records.
  With the API open, those rules are the only thing standing between a draft
  and the internet -- do not work around them.
* ``cors='*'`` is deliberate. No credentials are involved and the content is
  already destined for a public page, so an origin check would block nothing
  that curl cannot do anyway. Abuse is handled with rate limiting at the
  reverse proxy.
* Images are returned as URLs into /web/image rather than inlined base64, which
  keeps payloads small and lets any CDN cache them.
"""

import base64
import functools
import json
import re
from html import escape as html_escape

from odoo import http
from odoo.http import request

# Upper bound on how many records one call may return, so an anonymous caller
# cannot ask for an entire table in a single request.
MAX_LIMIT = 100
DEFAULT_LIMIT = 50

# Job application limits. The CV is written straight to ir.attachment, so these
# are the only thing between an open endpoint and someone filling the disk.
CV_MAX_BYTES = 10 * 1024 * 1024
CV_ALLOWED_EXTENSIONS = ('.pdf', '.doc', '.docx', '.odt', '.rtf', '.txt')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


REQUIRE_KEY_PARAM = 'x_dg_web_api.require_key'

# Tags that carry meaning even with no text around them, so a field holding only
# one of these is not empty.
_EMBEDDED = re.compile(r'<(img|iframe|video|audio|table|hr|svg)\b', re.I)
_TAGS = re.compile(r'<[^>]+>')


def _html(value):
    """Return a rich-text value, or None when it only looks non-empty.

    Odoo's editor stores `<p><br></p>` for a field the author cleared, and wraps
    saved content in `<div data-oe-version="2.0">`. Both are truthy strings, so
    a plain `or None` would report content the reader cannot see -- and the site
    would render a section heading with nothing beneath it. Strip the markup and
    decide on what is actually left.
    """
    if not value:
        return None
    raw = str(value)
    if _EMBEDDED.search(raw):
        return raw
    text = _TAGS.sub('', raw).replace('&nbsp;', ' ').replace('\xa0', ' ')
    return raw if text.strip() else None


def _plain_to_html(text):
    """Turn a plain-text message into safe HTML for an Odoo Html field."""
    text = (text or '').strip()
    if not text:
        return False
    return '<p>%s</p>' % html_escape(text).replace('\n', '<br>')


def api_key_optional(endpoint):
    """Enforce an API key only when the system parameter turns it on.

    The API is open by default: it serves content that is already destined for
    a public web page, and the front-end may call it from either the server or
    the browser. Setting `x_dg_web_api.require_key` to 1 in Settings > Technical
    > System Parameters starts requiring a key on every endpoint, without a code
    change -- useful if this is ever exposed somewhere it should not be open.

    Turning it on makes the API server-to-server only, since a key in browser
    code is a published key.

    Applied *under* @http.route, so the route decorator stays outermost.
    functools.wraps keeps the wrapped signature visible to inspect.signature,
    which is what Odoo's filter_kwargs uses to bind query-string arguments.
    """
    @functools.wraps(endpoint)
    def wrapper(self, *args, **kwargs):
        params = request.env['ir.config_parameter'].sudo()
        if params.get_param(REQUIRE_KEY_PARAM, '0') in ('1', 'true', 'True'):
            headers = request.httprequest.headers
            # Headers only, never the query string: URLs reach server logs and
            # Referer headers, and a key that leaks there has to be rotated.
            key = headers.get('X-API-Key') or ''
            if not key:
                authorization = headers.get('Authorization', '')
                if authorization[:7].lower() == 'bearer ':
                    key = authorization[7:].strip()
            # sudo(): the public user has no access to dg.api.client by design.
            # This lookup authenticates the caller, it does not read their data.
            if not request.env['dg.api.client'].sudo()._verify(key):
                return request.make_json_response({
                    'error': 'unauthorized',
                    'message': 'Missing or invalid API key. Send it as "Authorization: Bearer <key>".',
                }, status=401)
        return endpoint(self, *args, **kwargs)
    return wrapper


def _model(model_name, lang=None):
    """Return the model bound to the requested language.

    Titles, answers and article bodies are translate=True fields, so the public
    site must be able to ask for a specific language. The code is validated
    against the languages actually installed in Odoo rather than trusted, so an
    unknown or malicious value quietly falls back to the default instead of
    poisoning the context.
    """
    Model = request.env[model_name]
    if not lang:
        return Model
    installed = request.env['res.lang'].sudo().search([('code', '=', lang)], limit=1)
    return Model.with_context(lang=installed.code) if installed else Model


def _as_int(value, default, minimum=0, maximum=None):
    """Coerce a query-string argument to a sane int, falling back on `default`."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if number < minimum:
        return default
    if maximum is not None and number > maximum:
        return maximum
    return number


def _image_url(record, field):
    """Public URL for an image field, or None when the field is empty.

    /web/image is auth='public' and applies the same ACLs and record rules as
    this controller, so an unpublished record's image stays unreachable too.
    Append '/256x256' client-side to have Odoo resize on the fly.
    """
    if not record[field]:
        return None
    return f'/web/image/{record._name}/{record.id}/{field}'


def _paginated(Model, domain, serializer, limit, offset, order=None):
    """Shared list-endpoint envelope: total count plus a page of results."""
    records = Model.search(domain, limit=limit, offset=offset, order=order)
    return {
        'count': Model.search_count(domain),
        'limit': limit,
        'offset': offset,
        'results': [serializer(record) for record in records],
    }


# --------------------------------------------------------------------------
# Serializers
# --------------------------------------------------------------------------

def _banner_json(banner):
    return {
        'id': banner.id,
        'title': banner.name,
        'page': banner.page,
        'image': _image_url(banner, 'image'),
        'show_buttons': banner.show_buttons,
        'sequence': banner.sequence,
    }


def _faq_json(faq):
    return {
        'id': faq.id,
        'question': faq.name,
        'answer': faq.description,
        'sequence': faq.sequence,
    }


def _news_summary(news):
    return {
        'id': news.id,
        'title': news.name,
        'short_text': news.short_text,
        'publication_date': news.publication_date,
        'cover_image': _image_url(news, 'cover_image'),
    }


def _news_detail(news):
    return {
        **_news_summary(news),
        'detail_image': _image_url(news, 'detail_image'),
        'detailed_text': _html(news.detailed_text),
    }


def _job_summary(job):
    """The shape returned by the jobs list endpoint.

    department_id needs no sudo: hr.department carries its own public record
    rule ("Job department: Public") covering departments with a published job.

    contract_type_id and address_id do need it. The public user has no ACL at
    all on hr.contract.type, and the res.partner public rule limits it to its
    own commercial partner, so the office address is out of reach. Odoo's own
    public job template sudoes both for exactly this reason -- see
    website_hr_recruitment_templates.xml lines 121 and 129.
    """
    contract_type = job.contract_type_id.sudo()
    address = job.address_id.sudo()
    return {
        'id': job.id,
        'position_name': job.name,
        'company_name': job.web_company_name or job.company_id.sudo().name,
        'department': job.department_id.name or None,
        'employment_type': contract_type.name or None,
        'location': ', '.join(part for part in (address.city, address.country_id.name) if part) or None,
        'deadline': job.application_deadline,
        'published_date': job.published_date,
        # This points at the Odoo-hosted page, not your site. Keep it for the
        # "Apply" hand-off, or drop it and build your own detail URL from `id`.
        'odoo_url': job.full_url,
    }


def _job_detail(job):
    """Long-form fields, shaped to what the careers page actually renders.

    `requirements` here is web_requirements, not stock hr.job.requirements --
    the latter is restricted to HR users and would always read as empty for the
    public user this controller runs as.
    """
    return {
        **_job_summary(job),
        'openings': job.no_of_recruitment,
        'experience': job.web_experience or None,
        'description': _html(job.website_description),
        'responsibilities': _html(job.web_responsibilities),
        'requirements': _html(job.web_requirements),
        'benefits': _html(job.web_benefits),
    }


class DgWebApi(http.Controller):

    # Applied to every endpoint below. Requests run as Odoo's public user, so
    # the published-only record rules apply whether or not a key is enforced.
    # cors='*' lets the browser call this directly: no credentials are involved
    # and the content is already destined for a public page, so an origin check
    # would block nothing that curl cannot do anyway.
    _PUBLIC = dict(
        type='http', auth='public', methods=['GET'],
        cors='*', csrf=False, readonly=True, save_session=False,
    )

    # ---------------------------------------------------------------- banners
    @http.route('/api/v1/banners', **_PUBLIC)
    @api_key_optional
    def banners(self, page=None, lang=None, **kwargs):
        domain = [('page', '=', page)] if page in ('home', 'about_us') else []
        banners = _model('dg.banner', lang).search(domain)
        return request.make_json_response({
            'results': [_banner_json(banner) for banner in banners],
        })

    # -------------------------------------------------------------------- faq
    @http.route('/api/v1/faq', **_PUBLIC)
    @api_key_optional
    def faq(self, lang=None, **kwargs):
        entries = _model('dg.faq', lang).search([])
        return request.make_json_response({
            'results': [_faq_json(entry) for entry in entries],
        })

    # ------------------------------------------------------------------- news
    @http.route('/api/v1/news', **_PUBLIC)
    @api_key_optional
    def news(self, limit=None, offset=None, lang=None, **kwargs):
        return request.make_json_response(_paginated(
            _model('dg.news', lang), [], _news_summary,
            _as_int(limit, DEFAULT_LIMIT, minimum=1, maximum=MAX_LIMIT),
            _as_int(offset, 0),
        ))

    @http.route('/api/v1/news/<int:news_id>', **_PUBLIC)
    @api_key_optional
    def news_detail(self, news_id, lang=None, **kwargs):
        article = _model('dg.news', lang).search([('id', '=', news_id)], limit=1)
        if not article:
            return self._not_found('news article', news_id)
        return request.make_json_response(_news_detail(article))

    # ------------------------------------------------------------------- jobs
    @http.route('/api/v1/jobs', **_PUBLIC)
    @api_key_optional
    def jobs(self, limit=None, offset=None, department=None, lang=None, **kwargs):
        # No sudo(): website_hr_recruitment's public record rule already narrows
        # this to published jobs, so an unpublished draft cannot leak even if
        # this code is wrong.
        domain = [('department_id.name', '=ilike', department)] if department else []
        return request.make_json_response(_paginated(
            _model('hr.job', lang), domain, _job_summary,
            _as_int(limit, DEFAULT_LIMIT, minimum=1, maximum=MAX_LIMIT),
            _as_int(offset, 0),
            order='published_date desc, id desc',
        ))

    @http.route('/api/v1/jobs/<int:job_id>', **_PUBLIC)
    @api_key_optional
    def job_detail(self, job_id, lang=None, **kwargs):
        # search() rather than browse(): an unpublished id is filtered out by the
        # record rule and yields an empty set, which we turn into a clean 404.
        # browse().read() would raise AccessError and leak that the id exists.
        job = _model('hr.job', lang).search([('id', '=', job_id)], limit=1)
        if not job:
            return self._not_found('job position', job_id)
        return request.make_json_response(_job_detail(job))

    @http.route(
        '/api/v1/jobs/<int:job_id>/apply',
        type='http', auth='public', methods=['POST'],
        csrf=False, save_session=False,
    )
    @api_key_optional
    def job_apply(self, job_id, **kwargs):
        """Accept an application for a published job.

        Deliberately not readonly (it writes) and deliberately without CORS: the
        consuming site posts this from its own backend, where its spam
        protection lives, rather than from browser code. Odoo's generic
        /website/form/hr.applicant is avoided on purpose -- it never checks that
        job_id refers to a *published* job, and it answers 200 even on failure.
        """
        # No sudo(): the public record rule limits this to published jobs, so an
        # application cannot be attached to a draft position by guessing an id.
        job = request.env['hr.job'].search([('id', '=', job_id)], limit=1)
        if not job:
            return self._not_found('job position', job_id)

        name = (kwargs.get('name') or '').strip()
        email = (kwargs.get('email') or '').strip()
        errors = {}
        if not name:
            errors['name'] = 'Required.'
        if not email:
            errors['email'] = 'Required.'
        elif not EMAIL_RE.match(email):
            errors['email'] = 'Not a valid email address.'

        # Accept the file under any field name. The consuming site is free to
        # call it cv, resume, file or anything else, and a mismatch used to mean
        # the CV was silently dropped while the application still succeeded --
        # the worst possible failure for a job applicant.
        #
        # Errors are reported back under the name the caller actually used, so a
        # form that named the input "resume" can show "file too large" next to
        # that input instead of falling back to a generic error.
        cv_field = 'cv'
        upload = request.httprequest.files.get('cv')
        if not (upload and upload.filename):
            for field_name, uploaded in request.httprequest.files.items():
                if uploaded and uploaded.filename:
                    cv_field, upload = field_name, uploaded
                    break

        content = None
        if upload and upload.filename:
            if not upload.filename.lower().endswith(CV_ALLOWED_EXTENSIONS):
                errors[cv_field] = 'Allowed types: %s.' % ', '.join(CV_ALLOWED_EXTENSIONS)
            else:
                content = upload.read(CV_MAX_BYTES + 1)
                if len(content) > CV_MAX_BYTES:
                    errors[cv_field] = 'Larger than %d MB.' % (CV_MAX_BYTES // (1024 * 1024))

        if errors:
            return request.make_json_response(
                {'error': 'validation_error', 'fields': errors}, status=400)

        # sudo(): the public user has no create right on hr.applicant, and must
        # not be given one. Every value written below is validated above.
        applicant = request.env['hr.applicant'].sudo().create({
            'job_id': job.id,
            'partner_name': name,
            'email_from': email,
            'partner_phone': (kwargs.get('phone') or '').strip() or False,
            # Accept both spellings: 'linkedin' as documented, and
            # 'linkedin_profile' as Odoo names the field. A caller using the
            # latter previously had the value silently discarded.
            'linkedin_profile': (
                kwargs.get('linkedin') or kwargs.get('linkedin_profile') or ''
            ).strip() or False,
            # applicant_notes is an Html field, and this text comes from an
            # anonymous stranger that recruiters will later open in the backend.
            # Escape it and build the markup here rather than trusting the
            # field's own sanitiser to be the only line of defence.
            'applicant_notes': _plain_to_html(kwargs.get('message')),
        })

        if content:
            request.env['ir.attachment'].sudo().create({
                'name': upload.filename,
                'datas': base64.b64encode(content),
                'res_model': 'hr.applicant',
                'res_id': applicant.id,
            })

        return request.make_json_response({'ok': True, 'id': applicant.id}, status=201)

    # -------------------------------------------------------------- contact
    @http.route(
        '/api/v1/contact',
        type='http', auth='public', methods=['POST'],
        csrf=False, save_session=False,
    )
    @api_key_optional
    def contact(self, **kwargs):
        """Turn a website contact form submission into a helpdesk ticket.

        Takes a JSON body rather than form fields, because that is what the
        consuming site sends. type='http' is used instead of type='jsonrpc'
        deliberately: jsonrpc expects a JSON-RPC envelope, while this is a
        plain JSON object.

        Like the apply endpoint this writes, so it is not readonly, and it
        sends no CORS headers -- it is posted from the site's own origin.
        Nothing here rate-limits or checks a captcha; that belongs in front.
        """
        try:
            payload = json.loads(request.httprequest.get_data() or b'{}')
        except ValueError:
            return request.make_json_response(
                {'error': 'invalid_json', 'message': 'Body must be a JSON object.'},
                status=400)
        if not isinstance(payload, dict):
            return request.make_json_response(
                {'error': 'invalid_json', 'message': 'Body must be a JSON object.'},
                status=400)

        def field(key):
            value = payload.get(key)
            return value.strip() if isinstance(value, str) else ''

        name, email = field('name'), field('email')
        subject, question = field('subject'), field('question')
        phone, company = field('phone'), field('company')

        errors = {}
        for key, value in (('name', name), ('email', email),
                           ('subject', subject), ('question', question)):
            if not value:
                errors[key] = 'Required.'
        if email and not EMAIL_RE.match(email):
            errors['email'] = 'Not a valid email address.'
        if errors:
            return request.make_json_response(
                {'error': 'validation_error', 'fields': errors}, status=400)

        # The company has no dedicated field on a ticket, and creating a
        # res.partner for every enquiry would fill the database with junk from
        # an open endpoint. Keep it in the body where a reader will see it.
        description = _plain_to_html(question)
        if company:
            description = '<p><strong>Company:</strong> %s</p>%s' % (
                html_escape(company), description)

        if 'helpdesk.ticket' not in request.env:
            # Helpdesk is an Enterprise app. Checked here rather than up front
            # so a malformed request still gets a useful 400 on an instance
            # where Helpdesk happens to be missing.
            return request.make_json_response({
                'error': 'unavailable',
                'message': 'Helpdesk is not installed on this Odoo instance.',
            }, status=503)

        # sudo(): the public user cannot create tickets, and must not be able
        # to. Every value written below has been validated above.
        ticket = request.env['helpdesk.ticket'].sudo().create({
            'name': subject,
            'partner_name': name,
            'partner_email': email,
            'partner_phone': phone or False,
            'description': description,
        })
        return request.make_json_response({'ok': True, 'id': ticket.id}, status=201)

    # ----------------------------------------------------------------- shared
    @http.route('/api/v1/departments', **_PUBLIC)
    @api_key_optional
    def departments(self, lang=None, **kwargs):
        """Departments that currently have at least one published job.

        Derived from the published jobs themselves rather than read from
        hr.department directly, so the list can never name an empty department.
        """
        # _read_group rather than the deprecated read_group; it returns plain
        # tuples of (department_recordset, count).
        groups = _model('hr.job', lang)._read_group(
            domain=[('department_id', '!=', False)],
            groupby=['department_id'],
            aggregates=['__count'],
        )
        return request.make_json_response({
            'results': [{
                'id': department.id,
                'name': department.name,
                'job_count': count,
            } for department, count in groups],
        })

    def _not_found(self, label, record_id):
        return request.make_json_response(
            {'error': 'not_found', 'message': f'No published {label} with id {record_id}.'},
            status=404,
        )
