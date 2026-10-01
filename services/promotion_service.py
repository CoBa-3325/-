"""Promo-code service.

Only token and subscription rewards are supported. Roles are configured
through the ADMIN_IDS environment variable, not through promo codes.
"""
from __future__ import annotations
import logging
logger=logging.getLogger(__name__)

class PromotionService:
    def __init__(self, repository, settings, role_service=None):
        self.repository=repository; self.settings=settings; self.role_service=role_service

    @staticmethod
    def normalize(code):
        return ''.join(str(code or '').split()).upper()

    def can_create(self, actor_id, reward_type, admin_role=None):
        return bool(self.role_service and self.role_service.is_admin(actor_id))

    def create(self, actor_id, *, code, usage_type, reward_type, max_uses=None, token_amount=0, subscription_months=0, unlimited=False, admin_role=None, expires_at=None):
        if not self.can_create(actor_id,reward_type,admin_role):
            raise PermissionError('Недостаточно прав для создания этого промокода')
        if reward_type not in ('tokens','subscription'):
            raise ValueError('Неверный тип награды')
        if usage_type not in ('once','limited','unlimited'):
            raise ValueError('Неверный режим использования')
        if usage_type=='limited' and (max_uses is None or max_uses<1):
            raise ValueError('max_uses must be positive')
        return self.repository.create_promo(code=self.normalize(code),usage_type=usage_type,reward_type=reward_type,created_by=actor_id,max_uses=max_uses,token_amount=token_amount,subscription_months=subscription_months,is_unlimited_subscription=unlimited,admin_role=None,expires_at=expires_at)

    def redeem(self,user_id,code):
        result,error=self.repository.redeem_promo(user_id,self.normalize(code))
        if result:
            logger.info('PROMO_ACTIVATED user_id=%s promo_id=%s reward=%s',user_id,result['id'],result['reward_type'])
            return result
        return None

    # Backward-compatible no-op API kept for old integrations/tests.
    def token_promo_available(self,*args,**kwargs): return False
    def token_promo(self): return (10,0)
    def subscription_discount(self,*args,**kwargs): return 0
    def mark_token_promo_used(self,*args,**kwargs): return None
