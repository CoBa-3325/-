"""Сервис сохранения текстовых и голосовых сообщений в контекст."""

from datetime import datetime,timezone
from  schemas.message import MessageRecord
class MessageService:
    def __init__(self,repository): self.repository=repository
    def _save(self,**kw):
        m=MessageRecord(timestamp=datetime.fromtimestamp(kw.pop('timestamp_ms')/1000,tz=timezone.utc),**kw); return self.repository.save_message(m)
    def save_text(self,**kw): return self._save(source='text',**kw)
    def save_voice(self,**kw): return self._save(source='voice',**kw)
    def save_poll(self,**kw): return self._save(source='poll',**kw)
