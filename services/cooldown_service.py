"""Persistent per-chat LLM cooldown."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta

class CooldownService:
    def __init__(self, repository, minutes=10):
        self.repository = repository
        self.minutes = minutes

    def remaining_seconds(self, chat_id, now=None):
        now = now or datetime.now(timezone.utc)
        until = self.repository.get_cooldown_until(chat_id)
        if not until or until <= now:
            return 0
        return max(1, int((until-now).total_seconds()))

    def acquire(self, chat_id):
        return self.repository.acquire_llm_slot(chat_id)

    def activate(self, chat_id):
        self.repository.complete_llm(chat_id, datetime.now(timezone.utc) + timedelta(minutes=self.minutes))

    def release(self, chat_id):
        self.repository.release_llm(chat_id)
