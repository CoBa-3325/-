"""Сервис сохранения сообщений в контекст."""

from datetime import datetime, timezone
from schemas.message import MessageRecord

class MessageService:
    def __init__(self, repository):
        self.repository = repository

    def _save(self, **kw):
        message = MessageRecord(
            timestamp=datetime.fromtimestamp(kw.pop('timestamp_ms') / 1000, tz=timezone.utc),
            **kw,
        )
        return self.repository.save_message(message)

    def save_text(self, **kw):
        return self._save(source='text', **kw)

    def save_poll(self, **kw):
        return self._save(source='poll', **kw)
