from odoo import fields, models


class HrJob(models.Model):
    """Careers are mapped onto Odoo's real hr.job rather than a new CMS model.

    Reusing hr.job means the existing recruitment pipeline (applicants, stages,
    interviews) keeps working, and publishing is already governed by
    website_hr_recruitment's public record rule.

    Stock hr.job covers most of what the website shows:
        companyName          -> company_id (or web_company_name below)
        positionName         -> name
        location             -> address_id
        employmentType       -> contract_type_id
        description          -> website_description

    The fields below have no public hr.job equivalent. Note in particular that
    stock `hr.job.requirements` cannot be reused: it is declared with
    groups="hr.group_hr_user", so it is invisible to the public user and would
    read as empty over the API. `web_requirements` is a separate, public field.
    """
    _inherit = 'hr.job'

    application_deadline = fields.Datetime(
        string='Application Deadline',
        help='Last moment applications are accepted. Shown on the careers page.',
    )
    web_company_name = fields.Char(
        string='Company (website)', translate=True,
        help='Hiring entity as shown on the website. Falls back to the Odoo '
             'company when left empty, which is what most positions want.',
    )
    web_experience = fields.Char(
        string='Experience (website)', translate=True,
        help='Short free text, e.g. "3+ years".',
    )
    web_responsibilities = fields.Html(
        string='Responsibilities (website)', translate=True, sanitize=True,
        help='Rendered as a list on the careers page. Use a bulleted list.',
    )
    web_requirements = fields.Html(
        string='Requirements (website)', translate=True, sanitize=True,
        help='Public requirements. Distinct from the internal Requirements '
             'field, which is restricted to HR users.',
    )
    web_benefits = fields.Html(
        string='Benefits (website)', translate=True, sanitize=True,
        help='Rendered as a list on the careers page. Use a bulleted list.',
    )
