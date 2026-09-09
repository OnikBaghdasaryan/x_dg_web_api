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

import functools

from odoo import http
from odoo.http import request

# Upper bound on how many records one call may return, so an anonymous caller
# cannot ask for an entire table in a single request.
MAX_LIMIT = 100
DEFAULT_LIMIT = 50


REQUIRE_KEY_PARAM = 'x_dg_web_api.require_key'


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
        'detailed_text': news.detailed_text or None,
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
        'description': job.website_description or None,
        'responsibilities': job.web_responsibilities or None,
        'requirements': job.web_requirements or None,
        'benefits': job.web_benefits or None,
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
