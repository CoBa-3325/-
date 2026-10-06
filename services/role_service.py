"""Role checks.

Administrators are configured through the ADMIN_IDS environment variable and
identified by user ID. There is a single elevated role ("admin") with full
    access; legacy database-backed role grants are no longer used.
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


class RoleService:
    def __init__(self, repository, admin_ids=()):
        self.repository = repository
        self.admin_ids = {int(x) for x in (admin_ids or [])}

    def get_role(self, user_id):
        try:
            return 'admin' if int(user_id) in self.admin_ids else 'user'
        except (TypeError, ValueError):
            return 'user'

    def is_admin(self, user_id) -> bool:
        return self.get_role(user_id) == 'admin'

    def is_unlimited(self, user_id) -> bool:
        return self.is_admin(user_id)

    def can_access_admin_panel(self, user_id) -> bool:
        return self.is_admin(user_id)
