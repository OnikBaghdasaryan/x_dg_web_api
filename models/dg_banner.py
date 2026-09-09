from odoo import fields, models


class DgBanner(models.Model):
    """Hero banner for a page of the public site.

    Mirrors the admin-web "home-banner" and "about-us-banner" forms. Those are two
    separate endpoints there but one model here, keyed by `page`, because the two
    forms carry byte-identical fields -- adding a third page becomes a selection
    value rather than a new model, controller and set of ACLs.
    """
    _name = 'dg.banner'
    _description = 'Website Banner'
    _order = 'page, sequence, id'

    name = fields.Char(
        string='Title', required=True, translate=True,
        help='Max 30 characters in the current admin UI.',
    )
    page = fields.Selection(
        [('home', 'Home'), ('about_us', 'About Us')],
        string='Page', required=True, default='home',
    )
    image = fields.Image(string='Image', required=True, attachment=True)
    show_buttons = fields.Boolean(
        string='Show Buttons', default=False,
        help='Maps to isShowButtons in the admin-web banner form.',
    )
    sequence = fields.Integer(default=10)
    is_published = fields.Boolean(string='Published', default=False, index=True, copy=False)
