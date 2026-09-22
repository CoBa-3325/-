"""Centralized role hierarchy and authorization rules."""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)
ROLE_LEVEL = {'user': 0, 'admin': 1, 'creator': 2}

class RoleService:
    def __init__(self, repository):
        self.repository = repository

    def get_role(self, user_id: int) -> str:
        return self.repository.get_role(user_id)

    def is_admin(self, user_id: int) -> bool:
        return self.get_role(user_id) in ('admin', 'creator')

    def is_unlimited(self, user_id: int) -> bool:
        return self.get_role(user_id) in ('admin', 'creator')

    def can_manage(self, actor_id: int, target_id: int) -> bool:
        actor = self.get_role(actor_id)
        target = self.get_role(target_id)
        return actor == 'creator' and target == 'admin'

    def can_create_role_promo(self, actor_id: int, target_role: str) -> bool:
        actor = self.get_role(actor_id)
        return target_role == 'admin' and actor in ('admin', 'creator') or target_role == 'creator' and actor == 'creator'

    def can_view_creators(self, actor_id: int) -> bool:
        return self.get_role(actor_id) == 'creator'

    def can_access_admin_panel(self, actor_id: int) -> bool:
        return self.get_role(actor_id) in ('admin', 'creator')

    def set_role(self, actor_id: int, target_id: int, role: str) -> bool:
        if role not in ROLE_LEVEL:
            raise ValueError('unknown role')
        if role == 'user':
            if not self.can_manage(actor_id, target_id):
                raise PermissionError('actor cannot remove this role')
        else:
            raise PermissionError('role assignment is only available through promo activation')
        changed = self.repository.set_role_if_current(target_id, 'admin', 'user')
        if changed:
            logger.info('ROLE_CHANGED actor=%s target=%s admin->user', actor_id, target_id)
        return changed
