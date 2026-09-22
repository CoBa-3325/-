"""Paid and promo subscriptions. Role-based infinity is handled separately."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta

class SubscriptionService:
    def __init__(self, repository, role_service=None):
        self.repository = repository
        self.role_service = role_service

    def is_unlimited(self, user_id):
        return bool(self.role_service and self.role_service.is_unlimited(user_id)) or self.repository.has_unlimited_promo_subscription(user_id)

    def active_until(self, user_id, now=None):
        if self.is_unlimited(user_id):
            return None
        return self.repository.get_subscription_status(user_id, now)

    def is_active(self, user_id, now=None):
        if self.is_unlimited(user_id):
            return True
        return self.active_until(user_id, now) is not None

    def extend(self, user_id, months, now=None):
        if months not in (1, 3, 6):
            raise ValueError('subscription months must be 1, 3 or 6')
        now = now or datetime.now(timezone.utc)
        row = self.repository.get_subscription(user_id)
        if row and row['is_unlimited_subscription']:
            return None
        current_end = datetime.fromisoformat(row['ends_at']) if row and row['ends_at'] else now
        base = current_end if current_end > now else now
        start = datetime.fromisoformat(row['started_at']) if row and current_end > now else now
        end = base + timedelta(days=30 * months)
        self.repository.upsert_subscription(user_id, start, end, False, 'paid')
        self.repository.add_subscription_tokens(user_id, months, f'paid_subscription:{user_id}:{end.isoformat()}')
        return end

    def grant_promo(self, user_id, months=0, unlimited=False, source_id='promo'):
        if unlimited:
            self.repository.upsert_subscription(user_id, datetime.now(timezone.utc), None, True, 'promo')
            return None
        return self.extend(user_id, months)
