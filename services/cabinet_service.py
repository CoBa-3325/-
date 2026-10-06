"""User cabinet rendering."""
from datetime import datetime,timezone

class CabinetService:
    def __init__(self,repository,subscription_service,token_service,role_service=None):
        self.repository=repository;self.subscription_service=subscription_service;self.token_service=token_service;self.role_service=role_service
    def render(self,user_id):
        role=self.role_service.get_role(user_id) if self.role_service else 'user'
        lines=[f'Добро пожаловать, {self.repository.user_display_name(user_id)}','']
        if role=='admin':
            lines += ['Подписка: без ограничений']
        elif self.subscription_service.is_unlimited(user_id):
            lines += ['Подписка: без ограничений']
        else:
            end=self.subscription_service.active_until(user_id)
            if end:
                lines.append(f'Подписка действует до {end.astimezone(timezone.utc).strftime("%d.%m.%Y")}')
            else:
                lines.append('Подписка не оформлена или срок её действия закончился.')
            balance=self.token_service.balance(user_id)
            lines.append(f'Осталось бесплатных отчётов: {balance}')
        return '\n'.join(lines)
