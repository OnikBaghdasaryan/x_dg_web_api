import hashlib
import secrets

from odoo import _, fields, models


class DgApiClient(models.Model):
    """A consumer of the content API, identified by a bearer key.

    One record per consumer -- "snapp.am website (production)", "staging", a
    partner integration -- rather than a single shared secret, so a key can be
    rotated or revoked for one consumer without breaking the others.

    Only the SHA-256 digest of a key is stored. A plain digest is the right
    choice here, rather than a slow KDF like pbkdf2: keys are 256 bits of
    output from `secrets`, not user-chosen passwords, so there is no small
    search space for an attacker with the database to grind through, and the
    digest is looked up on every API call.
    """
    _name = 'dg.api.client'
    _description = 'Web API Client'
    _order = 'name'

    name = fields.Char(
        string='Consumer', required=True,
        help='Who this key was issued to, e.g. "snapp.am website (production)".',
    )
    key_hash = fields.Char(string='Key Digest', readonly=True, copy=False, index=True)
    key_hint = fields.Char(
        string='Key Ends With', readonly=True, copy=False,
        help='Last characters of the key, so an issued key can be identified without storing it.',
    )
    active = fields.Boolean(
        default=True,
        help='Untick to revoke this key immediately. Revocation takes effect on the next request.',
    )
    note = fields.Text(string='Notes')

    @staticmethod
    def _digest(key):
        return hashlib.sha256(key.encode()).hexdigest()

    def action_generate_key(self):
        """Issue a new key, replacing any existing one for this consumer."""
        self.ensure_one()
        key = secrets.token_urlsafe(32)
        self.write({
            'key_hash': self._digest(key),
            'key_hint': key[-6:],
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('API key for %s', self.name),
                'message': _('Copy this now. Only its digest is stored, so it cannot be shown again: %s', key),
                'type': 'warning',
                'sticky': True,
            },
        }

    def _verify(self, key):
        """Return the active client matching `key`, or an empty recordset.

        Callers must sudo() -- the public user has no access to this table by
        design, since reading it is an authentication step and not a data read.

        The lookup is an indexed equality match on the digest of the full key,
        so there is no secret-dependent comparison in Python to leak timing.
        `search` filters on active=True by default, which is what makes
        unticking Active a working revocation.
        """
        if not key:
            return self.browse()
        return self.search([('key_hash', '=', self._digest(key))], limit=1)
