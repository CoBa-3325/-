"""Типизированная запись сообщения, хранимая в контексте чата."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

MessageSource = Literal['text', 'poll']

@dataclass(slots=True)
class MessageRecord:
    max_message_id: str
    chat_id: int
    user_id: int | None
    user_name: str
    text: str
    source: MessageSource
    timestamp: datetime
    reply_to: str | None = None
