"""Promo-code service.

Only redemption is supported. Creating promo codes from the admin panel was
removed; administrators are configured through the ``ADMIN_IDS`` environment
variable.
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

    def redeem(self,user_id,code):
        result,error=self.repository.redeem_promo(user_id,self.normalize(code))
        if result:
            logger.info('PROMO_ACTIVATED user_id=%s promo_id=%s reward=%s',user_id,result['id'],result['reward_type'])
            return result
        return None
