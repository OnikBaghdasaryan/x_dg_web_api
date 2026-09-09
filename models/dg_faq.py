from odoo import fields, models


class DgFaq(models.Model):
    """A question/answer pair on the public site. Mirrors the "home-faq" form."""
    _name = 'dg.faq'
    _description = 'Website FAQ Entry'
    _order = 'sequence, id'

    name = fields.Char(string='Question', required=True, translate=True)
    description = fields.Text(string='Answer', required=True, translate=True)
    sequence = fields.Integer(default=10)
    is_published = fields.Boolean(string='Published', default=False, index=True, copy=False)
