{
    'name': 'DG Web API',
    'summary': 'Read-only public JSON API and CMS models for the external website',
    'description': """
Serves the content managed for the public website as a read-only, CORS-enabled
JSON API, so a fully separate front-end can consume it without authenticating.

Content types mirror the admin-web.snapp.am forms: home and about-us banners,
home FAQ, news articles, and careers (mapped onto Odoo's own hr.job so the
recruitment pipeline keeps working).
""",
    'version': '19.0.1.0.0',
    "website": "https://digitai.odoo.com/",
    "author": "Digitai LLC",
    'category': 'Website',
    'license': 'LGPL-3',
    'depends': ['website_hr_recruitment'],
    'data': [
        'security/dg_web_api_security.xml',
        'security/ir.model.access.csv',
        'views/dg_content_views.xml',
    ],
    'installable': True,
    'application': False,
}
