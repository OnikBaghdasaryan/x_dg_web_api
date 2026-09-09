# x_dg_web_api

Read-only JSON API that serves a public website's content out of Odoo 19, so a
fully separate front-end can render it without touching Odoo.

Built to replace the CMS half of an external admin panel: banners, FAQ, news and
careers move into Odoo, and the website reads them back over HTTP.

## Endpoints

All `GET`, all unauthenticated, CORS open to any origin.

| Endpoint | Returns |
|---|---|
| `/api/v1/banners?page=home\|about_us` | Hero banners for a page |
| `/api/v1/faq` | Question and answer pairs |
| `/api/v1/news` | Paginated article list |
| `/api/v1/news/{id}` | One article with its body |
| `/api/v1/jobs` | Paginated open positions |
| `/api/v1/jobs/{id}` | One position with long-form content |
| `/api/v1/departments` | Departments that currently have an open position |

`limit` (capped at 100), `offset` and `lang` are accepted where they apply.
The full contract, with response schemas and examples, is in
[`openapi.yaml`](openapi.yaml) — import it into Postman or generate a client
from it.

## Models

`dg.banner`, `dg.faq` and `dg.news` are new and editable under a **Web Content**
menu. Careers deliberately reuse Odoo's own `hr.job`, so the record that feeds
the website is the same one that collects applicants and CVs; the extra
website-only fields are added to `hr.job` rather than duplicated elsewhere.

## Security model

Requests execute as Odoo's **public user**, and every served model carries a
record rule limiting reads to published records. The controller never `sudo()`s
a search, so the ORM — not application code — is what keeps drafts off the
internet. Serializers hand-pick fields, so adding a field to a model can never
silently start publishing it.

Two fields do need `sudo()` and are commented as such: `hr.contract.type` has no
public ACL at all, and the `res.partner` public rule hides the office address.
Odoo's own public job template sudoes exactly the same two.

An optional API key can be switched on without a code change by setting the
system parameter `x_dg_web_api.require_key` to `1` and issuing a key under
**Web Content → API Clients**. Keys are stored as SHA-256 digests, shown once,
and revoked by unticking Active. Turning it on makes the API server-to-server
only, since a key in browser code is a published key.

## Deployment note

On a host serving several databases, an anonymous request cannot resolve which
database to answer from and returns `404`. Pin it at the reverse proxy for the
public paths rather than requiring every client to send a header — a browser
cannot send one anyway, because the CORS preflight carries no custom headers and
`<img>` tags carry none at all:

```nginx
location /api/ {
    proxy_pass http://odoo;
    proxy_set_header X-Odoo-Database <database>;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host  $host;
}
```

Repeat the forwarded headers as shown. nginx discards every inherited
`proxy_set_header` as soon as a location block defines one of its own, and
losing `X-Forwarded-Proto` makes Odoo generate `http://` URLs behind TLS.

Images are served by Odoo's stock `/web/image/<model>/<id>/<field>` route, which
also needs the same treatment.

## Requirements

Odoo 19. Depends on `website_hr_recruitment`.

## License

LGPL-3.
