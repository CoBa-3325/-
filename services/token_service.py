"""User-wide token pool with grant-level expiry and atomic consumption."""
from __future__ import annotations
import uuid

class TokenService:
    def __init__(self, repository, free_amount=30):
        self.repository = repository
        self.free_amount = free_amount

    def ensure_trial(self, user_id: int) -> None:
        self.repository.ensure_trial_grant(user_id, self.free_amount)

    def ensure(self, user_id, chat_id=None):
        # chat_id is accepted for backwards compatibility; tokens are user-wide.
        self.ensure_trial(user_id)

    def balance(self, user_id, chat_id=None) -> int:
        self.ensure_trial(user_id)
        return self.repository.get_token_balance(user_id)

    def deduct_for_llm(self, user_id, chat_id=None):
        operation_id = f'llm:{uuid.uuid4()}'
        return operation_id if self.repository.deduct_token(user_id, operation_id) else None

    def refund(self, user_id, chat_id, operation_id):
        return self.repository.refund_token(user_id, operation_id)

    def add(self, user_id, chat_id, amount, reason='purchase', token_type='purchased', expires_at=None, source_id=None):
        return self.repository.add_token_grant(user_id, amount, token_type, expires_at, source_id or f'{reason}:{uuid.uuid4()}')
