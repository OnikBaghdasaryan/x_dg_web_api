from odoo import fields, models


class DgNews(models.Model):
    """A news article. Mirrors the admin-web "news" form field for field."""
    _name = 'dg.news'
    _description = 'Website News Article'
    _order = 'publication_date desc, id desc'

    name = fields.Char(string='Title', required=True, translate=True)
    short_text = fields.Text(
        string='Short Text', required=True, translate=True,
        help='Teaser shown in the news list. shortText in admin-web.',
    )
    publication_date = fields.Datetime(
        string='Publication Date', required=True, default=fields.Datetime.now,
    )
    cover_image = fields.Image(
        string='Cover Image', attachment=True,
        help='Listing thumbnail. coverImage in admin-web.',
    )
    detail_image = fields.Image(
        string='Detail View Image', attachment=True,
        help='Header image on the article page. detailViewImage in admin-web.',
    )
    detailed_text = fields.Html(
        string='Detailed Text', translate=True, sanitize=True,
        help='Article body, authored in the rich-text editor.',
    )
    is_published = fields.Boolean(string='Published', default=False, index=True, copy=False)
