"""User cabinet rendering."""
from datetime import datetime,timezone
import math

class CabinetService:
    def __init__(self,repository,subscription_service,token_service,role_service=None):
        self.repository=repository;self.subscription_service=subscription_service;self.token_service=token_service;self.role_service=role_service
    def render(self,user_id):
        role=self.role_service.get_role(user_id) if self.role_service else 'user'
        if role=='admin':
            return 'Для вашего аккаунта отчёты доступны без ограничений.'
        elif self.subscription_service.is_unlimited(user_id):
            return 'Для вашего аккаунта отчёты доступны без ограничений.'
        end=self.subscription_service.active_until(user_id)
        if end:
            days=max(0,math.ceil((end-datetime.now(timezone.utc)).total_seconds()/86400))
            return (
                f'Подписка действует до {end.astimezone(timezone.utc).strftime("%d.%m.%Y")}.'
                f'\nДо окончания подписки осталось дней: {days}.'
            )
        balance=self.token_service.balance(user_id)
        return (
            'Если Вам понадобится больше возможностей, подписку можно оформить в меню бота. '
            'Это совершенно необязательно. '
            f'\nОсталось пробных отчётов: {balance}.'
        )
