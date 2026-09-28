"""Runtime configuration."""
from dataclasses import dataclass
import os
from dotenv import load_dotenv
load_dotenv()

def _required(name):
    value=os.getenv(name,'').strip()
    if not value or value.startswith('your_'):raise RuntimeError(f'Environment variable {name} is not configured')
    return value

def _int(name,default,minimum=0):
    try:v=int(os.getenv(name,str(default)))
    except ValueError as e:raise RuntimeError(f'Environment variable {name} must be integer') from e
    if v<minimum:raise RuntimeError(f'Environment variable {name} must be >= {minimum}')
    return v

@dataclass(frozen=True)
class Settings:
    max_bot_token:str; yc_api_key:str; yc_folder_id:str; yandex_gpt_model:str
    db_path:str; max_prompt_chars:int
    max_outgoing_message_chars:int
    free_tokens_per_user:int; token_price_10:int
    subscription_price_1_month:int; subscription_price_3_months:int; subscription_price_6_months:int; subscription_tokens_per_month:int
    subscription_expiration_warning_days:int; request_cooldown_minutes:int
    yookassa_shop_id:str|None; yookassa_secret_key:str|None; yookassa_return_url:str|None
    max_mode:str; max_webhook_url:str|None; max_webhook_secret:str|None; max_webhook_host:str; max_webhook_port:int

    @classmethod
    def from_env(cls):
        return cls(
            max_bot_token=_required('MAX_BOT_TOKEN'),yc_api_key=_required('YC_API_KEY'),yc_folder_id=_required('YC_FOLDER_ID'),
            yandex_gpt_model=os.getenv('YANDEX_GPT_MODEL','yandexgpt-5-lite'),
            db_path=os.getenv('DB_PATH','data/chat_history.db'),max_prompt_chars=_int('MAX_PROMPT_CHARS',15000,1000),
            max_outgoing_message_chars=_int('MAX_OUTGOING_MESSAGE_CHARS',3500,100),
            free_tokens_per_user=_int('FREE_TOKENS_PER_USER',30,0),token_price_10=_int('TOKEN_PRICE_10',300,0),
            subscription_price_1_month=_int('SUBSCRIPTION_PRICE_1_MONTH',99,0),subscription_price_3_months=_int('SUBSCRIPTION_PRICE_3_MONTHS',259,0),subscription_price_6_months=_int('SUBSCRIPTION_PRICE_6_MONTHS',559,0),subscription_tokens_per_month=_int('SUBSCRIPTION_TOKENS_PER_MONTH',30,0),
            subscription_expiration_warning_days=_int('SUBSCRIPTION_EXPIRATION_WARNING_DAYS',1,0),request_cooldown_minutes=_int('REQUEST_COOLDOWN_MINUTES',10,0),
            yookassa_shop_id=os.getenv('YOOKASSA_SHOP_ID') or None,yookassa_secret_key=os.getenv('YOOKASSA_SECRET_KEY') or None,yookassa_return_url=os.getenv('YOOKASSA_RETURN_URL') or None,
            max_mode=os.getenv('MAX_MODE','polling').lower(),max_webhook_url=os.getenv('MAX_WEBHOOK_URL') or None,max_webhook_secret=os.getenv('MAX_WEBHOOK_SECRET') or None,max_webhook_host=os.getenv('MAX_WEBHOOK_HOST','0.0.0.0'),max_webhook_port=_int('MAX_WEBHOOK_PORT',8080,1)
        )
