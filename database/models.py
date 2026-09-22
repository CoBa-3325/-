"""Модели/структуры данных, используемые слоем базы данных."""

from dataclasses import dataclass
from datetime import datetime

@dataclass(slots=True)
class MessageModel:
    id:int|None; max_message_id:str; chat_id:int; user_id:int|None; user_name:str; text:str; source:str; timestamp:datetime; reply_to:str|None=None
@dataclass(slots=True)
class ChatSettingsModel:
    chat_id:int; enabled:bool; interval_type:str; custom_days:int|None; summary_hour:int; timezone:str
@dataclass(slots=True)
class SummaryRunModel:
    id:int|None; chat_id:int; period_start:datetime; period_end:datetime; created_at:datetime; summary_text:str
@dataclass(slots=True)
class UserModel:
    user_id:int; first_name:str|None; last_name:str|None; created_at:datetime; updated_at:datetime
@dataclass(slots=True)
class ChatModel:
    chat_id:int; chat_type:str; title:str|None; created_at:datetime; updated_at:datetime
@dataclass(slots=True)
class UserChatBalanceModel:
    user_id:int; chat_id:int; tokens:int; free_granted:bool; promo_used:bool; created_at:datetime; updated_at:datetime
@dataclass(slots=True)
class SubscriptionModel:
    user_id:int; started_at:datetime; ends_at:datetime; created_at:datetime; updated_at:datetime
@dataclass(slots=True)
class OrderModel:
    order_id:str; user_id:int; payment_id:str|None; product_type:str; tariff:str; chat_id:int|None; token_amount:int; subscription_months:int; original_amount:int; discount_percent:int; discount_amount:int; final_amount:int; status:str; created_at:datetime; updated_at:datetime; paid_at:datetime|None
