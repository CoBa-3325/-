"""YooKassa orders with webhook and explicit payment-status verification."""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from payment.yookassa import YooKassaClient

logger = logging.getLogger(__name__)


class PaymentService:
    def __init__(self, repository, settings, subscription_service, token_service):
        self.repository = repository
        self.settings = settings
        self.subscription = subscription_service
        self.yookassa = YooKassaClient(settings.yookassa_shop_id, settings.yookassa_secret_key)

    def _price(self, product, tariff):
        if product == 'subscription':
            mapping = {
                '1_month': (self.settings.subscription_price_1_month, 0, 1),
                '3_months': (self.settings.subscription_price_3_months, 0, 3),
                '6_months': (self.settings.subscription_price_6_months, 0, 6),
            }
            if tariff in mapping:
                return mapping[tariff]
        raise ValueError('unknown product/tariff')

    async def create_order(self, user_id, product, tariff, chat_id=None):
        original, token_amount, months = self._price(product, tariff)
        order_id = f'order_{uuid.uuid4().hex}'
        now = self.repository.now()
        row = (
            order_id, user_id, None, product, tariff, chat_id, token_amount, months,
            original, original, 'pending', now, now, None,
        )
        self.repository.create_order(row)
        try:
            payment = await self.yookassa.create_payment(
                Decimal(str(original)),
                f'context-bot {product} {tariff}',
                order_id,
                self.settings.yookassa_return_url,
            )
            self.repository.set_payment_id(order_id, payment['id'])
            payment_url = payment.get('confirmation', {}).get('confirmation_url')
            self.repository.set_payment_url(order_id, payment_url)
            logger.info(
                'PAYMENT_CREATED order_id=%s payment_id=%s user_id=%s product=%s tariff=%s',
                order_id, payment['id'], user_id, product, tariff,
            )
            return self.repository.get_order(order_id), payment_url
        except Exception:
            self.repository.set_order_status(order_id, 'failed')
            logger.exception('PAYMENT_CREATE_FAILED order_id=%s', order_id)
            raise

    async def _verify_payment_object(self, order, payment):
        """Проверить объект YooKassa и, если он успешен, выдать товар."""
        status = payment.get('status')
        if status != 'succeeded':
            if status in ('canceled', 'cancelled') and order['status'] == 'pending':
                self.repository.set_order_status(order['order_id'], 'cancelled')
            logger.info(
                'PAYMENT_NOT_SUCCEEDED order_id=%s payment_id=%s status=%s',
                order['order_id'], order['payment_id'], status,
            )
            return None

        amount = payment.get('amount', {}).get('value')
        currency = payment.get('amount', {}).get('currency')
        if currency != 'RUB' or Decimal(str(amount)) != Decimal(str(order['final_amount'])):
            raise ValueError('payment amount/currency mismatch')

        metadata = payment.get('metadata') or {}
        if metadata.get('order_id') != order['order_id']:
            raise ValueError('payment order mismatch')

        if order['status'] == 'paid':
            return True
        if order['status'] != 'pending':
            return False

        now = datetime.now(timezone.utc)
        row = self.repository.get_subscription(order['user_id'])
        if row and row['is_unlimited_subscription']:
            logger.warning(
                'PAYMENT_FULFILLMENT_SKIPPED_UNLIMITED order_id=%s user_id=%s',
                order['order_id'], order['user_id'],
            )
            return False

        current_end = (
            datetime.fromisoformat(row['ends_at'])
            if row and row['ends_at']
            else now
        )
        base = current_end if current_end > now else now
        start = (
            datetime.fromisoformat(row['started_at'])
            if row and current_end > now
            else now
        )
        end = base + timedelta(days=30 * order['subscription_months'])
        result = self.repository.fulfill_order(
            order['order_id'],
            subscription_start=start, subscription_end=end,
            token_amount=0,
        )

        logger.info(
            'PAYMENT_FULFILLED payment_id=%s order_id=%s result=%s',
            order['payment_id'], order['order_id'], result,
        )
        return result

    async def handle_webhook_payment(self, payment_id):
        """Обработать payment.succeeded webhook. Операция идемпотентна."""
        if not payment_id:
            return False
        order = self.repository.get_order_by_payment(payment_id)
        if not order:
            logger.warning('PAYMENT_WEBHOOK_ORDER_NOT_FOUND payment_id=%s', payment_id)
            return False
        payment = await self.yookassa.get_payment(payment_id)
        return await self._verify_payment_object(order, payment)

    async def verify_order(self, order_id, user_id=None):
        """Явно проверить оплату через YooKassa.

        Возвращает:
        - True — товар начислен или уже был начислен;
        - None — платеж ещё не успешен;
        - False — заказ отменён/не может быть выдан.
        """
        order = self.repository.get_order(order_id)
        if not order:
            raise ValueError('order not found')
        if user_id is not None and order['user_id'] != user_id:
            raise PermissionError('order does not belong to user')
        if order['status'] == 'paid':
            return True
        if order['status'] != 'pending':
            return False
        if not order['payment_id']:
            return None

        payment = await self.yookassa.get_payment(order['payment_id'])
        return await self._verify_payment_object(order, payment)

    async def retry_order(self, order_id, user_id=None):
        order = self.repository.get_order(order_id)
        if not order:
            raise ValueError('order not found')
        if user_id is not None and order['user_id'] != user_id:
            raise PermissionError('order does not belong to user')
        if order['status'] not in ('failed', 'cancelled'):
            raise ValueError('only failed/cancelled orders can be retried')
        return await self.create_order(
            order['user_id'], order['product_type'], order['tariff'], order['chat_id']
        )

    def cancel_order(self, order_id, user_id=None):
        order = self.repository.get_order(order_id)
        if not order or order['status'] != 'pending':
            return False
        if user_id is not None and order['user_id'] != user_id:
            return False
        self.repository.set_order_status(order_id, 'cancelled')
        return True

    async def send_payment_reminder(self, order, bot):
        """Recheck an unpaid subscription and send one delayed payment reminder."""
        current = self.repository.get_order(order['order_id'])
        if not current or current['status'] != 'pending' or not current['payment_url']:
            return False
        if not current['payment_id']:
            return False

        payment = await self.yookassa.get_payment(current['payment_id'])
        await self._verify_payment_object(current, payment)

        # Successful/cancelled payments are no longer pending after verification.
        current = self.repository.get_order(current['order_id'])
        if not current or current['status'] != 'pending':
            return False

        from maxapi.enums.parse_mode import ParseMode
        from maxapi.utils.formatting import UserMention

        mention = UserMention('Здравствуйте!', user_id=int(current['user_id'])).as_html()
        message = (
            f'{mention}\n\nВы оформляли подписку, но оплата пока не завершена. '
            'Если Вы всё ещё хотите её оформить, можно вернуться к оплате по ссылке ниже. '
            'Если планы изменились — просто проигнорируйте это сообщение.\n\n'
            f'<a href="{current["payment_url"]}">Перейти к оплате</a>'
        )

        # Claim before network delivery so concurrent scheduler runs cannot duplicate it.
        if not self.repository.claim_payment_reminder(current['order_id']):
            return False
        try:
            await bot.send_message(
                user_id=int(current['user_id']),
                text=message,
                format=ParseMode.HTML,
            )
        except Exception:
            self.repository.release_payment_reminder_claim(current['order_id'])
            raise
        logger.info('PAYMENT_REMINDER_SENT order_id=%s user_id=%s', current['order_id'], current['user_id'])
        return True
