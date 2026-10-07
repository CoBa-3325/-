"""MAX event handlers implementing the complete user/admin flow."""
from __future__ import annotations

import contextlib
import logging
import math
from datetime import datetime, timezone

from maxapi import types

from bot.commands import GROUP_HELP_TEXT, PRIVATE_HELP_TEXT
from bot.keyboards import *
from services.period_service import period_for_interval
from schemas.message import MessageRecord

logger = logging.getLogger(__name__)

REPORT_FEEDBACK_NOTE = (
    '❤️ Хотите сделать отчёт ещё лучше?\n'
    'Или у вас возник вопрос по моей работе?\n'
    'Напишите мне в личные сообщения **/помощь**.'
)

def _get(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _command(text):
    if not text or not text.startswith('/'):
        return None, []
    parts = text.strip().split()
    return parts[0].split('@', 1)[0].lower(), parts[1:]


def _chat_type(message):
    r = _get(message, 'recipient')
    return str(_get(r, 'chat_type') or _get(r, 'type') or 'unknown').lower()


def _user(message, event=None):
    sender = _get(event, 'from_user') or _get(message, 'sender')
    uid = _get(sender, 'user_id')
    first = _get(sender, 'first_name', '')
    last = _get(sender, 'last_name', '')
    name = ' '.join(x for x in (first, last) if x).strip() or str(uid or 'Пользователь')
    return uid, name, first, last


def _user_greeting(message, event=None):
    sender = _get(event, 'from_user') or _get(message, 'sender')
    _, name, _, _ = _user(message, event)
    username = _get(sender, 'username')
    return f'{name} (@{username})' if username else name


def _message_meta(event):
    m = _get(event, 'message')
    b = _get(m, 'body')
    r = _get(m, 'recipient')
    return (
        m,
        b,
        _get(r, 'chat_id'),
        str(_get(b, 'mid') or _get(m, 'message_id') or ''),
        _get(m, 'timestamp') or _get(event, 'timestamp'),
    )


def _inline(rows):
    return [types.ButtonsPayload(buttons=rows).pack()]


async def _send(event, text, settings, attachments=None):
    size = settings.max_outgoing_message_chars
    chunks = [text[i:i + size] for i in range(0, len(text), size)] or ['']
    sent = []
    for i, c in enumerate(chunks):
        sent.append(await event.message.answer(c, attachments=attachments if i == 0 else None, format='markdown'))
    return sent


async def _delete_max_message(bot, message_id):
    """Удаляет сообщение через официальный MAX API.

    Используем HTTP-клиент maxapi (``bot.delete_message``), а не отдельную
    aiohttp-сессию: у клиента maxapi корректно настроены доверенные
    сертификаты, тогда как собственная сессия на сервере падает с
    ``CERTIFICATE_VERIFY_FAILED``.

    В ЛС MAX разрешает боту удалять только собственные сообщения.
    В группе бот с правом удаления может удалить и сообщение пользователя.
    """
    if not message_id or bot is None:
        return False
    try:
        result = await bot.delete_message(str(message_id))
        ok = bool(getattr(result, 'success', True))
        if not ok:
            logger.warning('MAX_MESSAGE_DELETE_REJECTED message_id=%s result=%r', message_id, result)
        return ok
    except Exception:
        logger.warning('MAX_MESSAGE_DELETE_FAILED message_id=%s', exc_info=True)
        return False


async def _delete_callback_message(event):
    """Удаляет старое сообщение бота только в ЛС.

    В группах сообщения бота при навигации не удаляются.
    """
    message = _get(event, 'message')
    if _chat_type(message) == 'chat':
        return False
    body = _get(message, 'body') or {}
    message_id = str(_get(body, 'mid') or _get(message, 'message_id') or '')
    if not message_id:
        return False
    return await _delete_max_message(_get(event, 'bot'), message_id)


async def _delete_report_step(event):
    """Delete the bot's current report-wizard message in a group."""
    message = _get(event, 'message')
    sender = _get(message, 'sender')
    if sender is not None and not _get(sender, 'is_bot', False):
        logger.warning('REPORT_STEP_DELETE_NOT_BOT_MESSAGE')
        return False
    body = _get(message, 'body') or {}
    message_id = str(_get(body, 'mid') or _get(message, 'message_id') or '')
    if not message_id:
        logger.warning('REPORT_STEP_DELETE_NO_MESSAGE_ID')
        return False
    return await _delete_max_message(_get(event, 'bot'), message_id)


def _is_report_navigation(payload):
    return (
        payload in ('summary_menu', 'summary_people_done', 'summary_cancel')
        or payload.startswith('summary_type:')
        or payload.startswith('summary_period:')
        or payload.startswith('summary_people_toggle:')
        or payload.startswith('summary_people_period:')
    )


def _is_bot_added_greeting(message):
    body = _get(message, 'body') or {}
    text = str(_get(body, 'text') or '').strip()
    return text == GROUP_HELP_TEXT.strip()


def _role(services, uid):
    return services.roles.get_role(uid)


def _non_report_attachments(event, attachments):
    # В беседах оставляем только кнопки, относящиеся к /отчет.
    return None if _chat_type(_get(event, 'message')) == 'chat' else attachments


async def _send_start_message(services, send, uid):
    """Единая реализация приветствия для команды и нативной кнопки Start в ЛС."""
    services.repository.upsert_user(uid)
    await send(
        PRIVATE_HELP_TEXT,
        attachments=private_start_menu(
            services.subscription.is_active(uid),
            _role(services, uid),
        ),
    )


def register_handlers(dp, services):

    async def show_subscription(event, uid):
        if _role(services, uid) != 'user':
            await event.message.answer('Для административных ролей подписка бесконечная.')
            return
        end = services.subscription.active_until(uid)
        now = datetime.now(timezone.utc)
        if end:
            txt = (
                f'Дата окончания подписки {end.astimezone(timezone.utc).strftime("%d.%m.%Y")}\n'
                f'Дней до конца {max(0, math.ceil((end - now).total_seconds() / 86400))}'
            )
        else:
            txt = 'У вас не оформлена подписка\nДней до конца 0'
        await event.message.answer(
            txt,
            attachments=_non_report_attachments(
                event,
                subscription_keyboard(
                    services.settings_config.subscription_price_1_month,
                    services.settings_config.subscription_price_3_months,
                    services.settings_config.subscription_price_6_months,
                ),
            ),
        )

    async def generate_summary(event, uid, chat_id, days, greeting, source_user_message_id=None):
        remaining = services.cooldown.remaining_seconds(chat_id)
        if remaining:
            await event.message.answer(f'{greeting}, подождите ещё {math.ceil(remaining / 60)} минут до следующего запроса.')
            return
        if not services.cooldown.acquire(chat_id):
            remaining = services.cooldown.remaining_seconds(chat_id)
            await event.message.answer(
                f'{greeting}, подождите ещё {math.ceil(remaining / 60)} минут до следующего запроса.'
                if remaining else f'{greeting}, в этом чате уже выполняется запрос.'
            )
            return
        period = period_for_interval('custom', custom_days=days)
        rows = services.repository.get_messages(chat_id, period.start, period.end)
        if not rows:
            services.cooldown.release(chat_id)
            await event.message.answer(f'{greeting}, за выбранный период сообщений не найдено.')
            return
        unlimited = services.subscription.is_active(uid)
        operation = None
        if not unlimited:
            operation = services.tokens.deduct_for_llm(uid, chat_id)
            if operation is None:
                services.cooldown.release(chat_id)
                await event.message.answer(
                    f'Уважаемый {greeting}, недостаточно отчётов для создания сводки. '
                    'Перейдите в личные сообщения бота для оформления подписки.'
                )
                return
        llm_succeeded = False
        try:
            result = await services.summary.generate(chat_id=chat_id, start=period.start, end=period.end)
            llm_succeeded = True
            services.summary.save_run(chat_id, period.start, period.end, result)
            services.cooldown.activate(chat_id)
            await _send(
                event,
                f'Уважаемый {greeting}, вот сводка за выбранный вами период времени:\n'
                + f'📊 Отчёт за {days} ' + ('день' if days == 1 else 'дня' if 2 <= days <= 4 else 'дней')
                + '\n\n' + result + '\n\n' + REPORT_FEEDBACK_NOTE,
                services.settings_config,
            )
            # Команду пользователя /отчет не удаляем.
        except Exception:
            if operation and not llm_succeeded:
                services.tokens.refund(uid, chat_id, operation)
            services.cooldown.release(chat_id)
            logger.exception('LLM_REQUEST_FAILED chat_id=%s user_id=%s', chat_id, uid)
            await event.message.answer(f'{greeting}, не удалось сформировать отчёт. Лимит отчётов восстановлен, если он был списан.')

    async def generate_summary_custom(event, uid, chat_id, start, end, title, greeting, user_ids=None,
                                      source_user_message_id=None):
        remaining = services.cooldown.remaining_seconds(chat_id)
        if remaining:
            await event.message.answer(f'{greeting}, подождите ещё {math.ceil(remaining / 60)} минут до следующего запроса.')
            return
        if not services.cooldown.acquire(chat_id):
            remaining = services.cooldown.remaining_seconds(chat_id)
            await event.message.answer(
                f'{greeting}, подождите ещё {math.ceil(remaining / 60)} минут до следующего запроса.'
                if remaining else f'{greeting}, в этом чате уже выполняется запрос.'
            )
            return
        rows = services.repository.get_messages(chat_id, start, end, user_ids=user_ids)
        if not rows:
            services.cooldown.release(chat_id)
            await event.message.answer(f'{greeting}, за выбранный период сообщений нет.')
            return
        op = None
        if not services.subscription.is_active(uid):
            op = services.tokens.deduct_for_llm(uid, chat_id)
            if op is None:
                services.cooldown.release(chat_id)
                await event.message.answer(
                    f'Уважаемый {greeting}, недостаточно отчётов для создания сводки. '
                    'Перейдите в личные сообщения бота для оформления подписки.'
                )
                return
        llm_succeeded = False
        try:
            result = await services.summary.generate(chat_id=chat_id, start=start, end=end, user_ids=user_ids)
            llm_succeeded = True
            services.summary.save_run(chat_id, start, end, result)
            services.cooldown.activate(chat_id)
            await _send(
                event,
                f'Уважаемый {greeting}, вот сводка за выбранный вами период времени:\n\n'
                f'📊 Отчёт за {title}\n\n{result}\n\n{REPORT_FEEDBACK_NOTE}',
                services.settings_config,
            )
            # Команду пользователя /отчет не удаляем.
        except Exception:
            if op and not llm_succeeded:
                services.tokens.refund(uid, chat_id, op)
            services.cooldown.release(chat_id)
            logger.exception('LLM_REQUEST_FAILED chat_id=%s user_id=%s', chat_id, uid)
            await event.message.answer(f'{greeting}, не удалось сформировать отчёт. Лимит отчётов восстановлен, если он был списан.')

    async def send_ticket_update(bot, ticket, sender_type, text):
        """Deliver a ticket message to the other side and persist its history."""
        ticket_id = int(ticket['ticket_id'])
        if sender_type == 'user':
            recipient = int(services.settings_config.support_user_id)
            label = '📩 Новый запрос в поддержку'
            sender_id = int(ticket['user_id'])
            keyboard = support_reply_keyboard(ticket_id)
        else:
            recipient = int(ticket['user_id'])
            label = '💬 Ответ поддержки'
            sender_id = int(services.settings_config.support_user_id)
            keyboard = ticket_user_keyboard(ticket_id)
        when = datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M UTC')
        message_text = (
            f'{label} №{ticket_id} · {when}\n'
            f'Пользователь: {ticket["user_name"]} (ID {ticket["user_id"]})\n\n{text}'
        )
        try:
            await bot.send_message(user_id=recipient, text=message_text, attachments=keyboard)
            services.repository.add_support_ticket_message(ticket_id, sender_type, sender_id, text)
            return True
        except Exception:
            logger.exception('SUPPORT_TICKET_DELIVERY_FAILED ticket_id=%s recipient=%s', ticket_id, recipient)
            return False

    async def forward_support_message(event, uid, text):
        if _chat_type(_get(event, 'message')) in ('chat', 'channel'):
            services.repository.clear_user_state(uid)
            return False
        message, _, _, _, _ = _message_meta(event)
        _, name, _, _ = _user(message, event)
        ticket = services.repository.get_open_support_ticket(uid)
        if ticket is None:
            ticket = services.repository.create_support_ticket(uid, name)
        if not await send_ticket_update(event.bot, ticket, 'user', text):
            await event.message.answer(
                'Не удалось передать сообщение в поддержку. Попробуйте позже.'
            )
            return
        await event.message.answer(
            f'Ваш запрос {ticket["ticket_id"]} передан в поддержку. '
            'Пока запрос открыт Вы можете писать сообщения с предложениями или описанием проблемы. '
            'Поддержка ответит Вам в ближайшее время.'
        )

    async def deliver_support_reply(event, uid, ticket_id, text):
        ticket = services.repository.get_support_ticket(ticket_id)
        if not ticket or ticket['status'] != 'open':
            services.repository.clear_user_state(uid)
            await event.message.answer('Обращение уже закрыто или не найдено.')
            return
        if await send_ticket_update(event.bot, ticket, 'support', text):
            services.repository.clear_user_state(uid)
            await event.message.answer(f'✅ Ответ отправлен по запросу №{ticket_id}.')
        else:
            await event.message.answer('Не удалось доставить ответ. Запрос остаётся открытым.')

    async def handle_state_input(event, uid, text, state):
        name, data = state
        if name == 'support_await':
            services.repository.clear_user_state(uid)
            await forward_support_message(event, uid, text)
            return True
        if name == 'support_reply':
            await deliver_support_reply(event, uid, int(data['ticket_id']), text)
            return True
        return False

    async def send_payment(event, order, link):
        if link:
            await event.message.answer(
                f'Заказ {order["order_id"]} создан.',
                attachments=_inline([
                    [{'type': 'link', 'text': 'Перейти к оплате', 'url': link}],
                    [{'type': 'callback', 'text': '✅ Проверить оплату',
                      'payload': f'payment_check:{order["order_id"]}'}],
                    [{'type': 'callback', 'text': 'Отмена', 'payload': f'cancel:{order["order_id"]}'}],
                ]),
            )
        else:
            await event.message.answer('Не удалось получить ссылку на оплату.')

    @dp.bot_started()
    async def bot_started(event):
        # bot_started приходит только для личного диалога с ботом.
        # Это именно нативная кнопка MAX «Начать», поэтому отдельную
        # inline-кнопку в ЛС создавать не нужно.
        uid = _get(_get(event, 'user'), 'user_id')
        if uid is None:
            uid = getattr(event, 'user_id', None)
        chat_id = _get(event, 'chat_id')
        if uid is None or chat_id is None:
            logger.warning('BOT_STARTED_INVALID_EVENT event=%r', event)
            return
        services.repository.upsert_user(uid)
        services.repository.upsert_chat(chat_id, 'dialog')
        await _send_start_message(
            services,
            lambda text, attachments=None: event.bot.send_message(
                chat_id=chat_id, text=text, attachments=attachments
            ),
            uid,
        )

    @dp.bot_added()
    async def bot_added(event):
        # Бот добавлен в чат/канал: сохраняем его и показываем инструкцию.
        chat_id = _get(event, 'chat_id')
        if chat_id is None:
            return
        chat_type = 'channel' if _get(event, 'is_channel', False) else 'chat'
        title = None
        try:
            chat = await event.bot.get_chat_by_id(int(chat_id))
            title = _get(chat, 'title')
        except Exception:
            logger.exception('BOT_ADDED_CHAT_LOOKUP_FAILED chat_id=%s', chat_id)
        services.repository.upsert_chat(chat_id, chat_type, title)
        if chat_type == 'chat':
            try:
                await event.bot.send_message(
                    chat_id=int(chat_id),
                    text=GROUP_HELP_TEXT,
                    attachments=group_added_keyboard(),
                )
            except Exception:
                logger.exception('BOT_ADDED_GREETING_FAILED chat_id=%s', chat_id)

    @dp.bot_removed()
    async def bot_removed(event):
        # MAX сообщает это событие, когда бот удалён из групповой беседы.
        chat_id = _get(event, 'chat_id')
        if chat_id is None:
            return
        removed = services.repository.delete_chat(chat_id)
        logger.info('BOT_REMOVED_CHAT_DB_CLEANUP chat_id=%s removed=%s', chat_id, removed)

    @dp.chat_title_changed()
    async def chat_title_changed(event):
        chat_id = _get(event, 'chat_id')
        title = _get(event, 'title')
        if chat_id is not None and title:
            services.repository.update_chat_title(chat_id, title)

    @dp.message_created()
    async def message_created(event):
        message, body, chat_id, mid, timestamp = _message_meta(event)
        if chat_id is None or not mid or timestamp is None:
            return
        uid, name, first, last = _user(message, event)
        if uid is None:
            return
        sender = _get(message, 'sender')
        if _get(sender, 'is_bot', False):
            return
        ctype = _chat_type(message)
        services.repository.upsert_user(uid, first, last)
        services.repository.upsert_chat(chat_id, ctype)
        text = (_get(body, 'text') or '').strip()
        command, _ = _command(text)

        # Persistent input flows survive bot restarts.
        state = services.repository.get_user_state(uid)
        if state and not text.startswith('/') and ctype not in ('chat', 'channel'):
            if await handle_state_input(event, uid, text, state):
                return

        # Every non-command message in the user's DM continues the open ticket.
        if ctype not in ('chat', 'channel') and text and not text.startswith('/'):
            ticket = services.repository.get_open_support_ticket(uid)
            if ticket:
                await forward_support_message(event, uid, text)
                return

        if command in ('/начать',):
            help_text = GROUP_HELP_TEXT if ctype == 'chat' else PRIVATE_HELP_TEXT
            attachments = (
                group_start_keyboard() if ctype == 'chat'
                else main_menu(services.subscription.is_active(uid), _role(services, uid), ctype)
            )
            await _send(event, help_text, services.settings_config, attachments)
            return
        if command == '/помощь':
            if ctype == 'chat':
                await event.message.answer('Обращения в поддержку принимаются в личных сообщениях с ботом.')
                return
            services.repository.set_user_state(uid, 'support_await', {})
            await event.message.answer(
                'Опишите ваш вопрос одним сообщением — я передам его в поддержку.',
                attachments=back_keyboard(),
            )
            return
        if command == '/отчет':
            if ctype != 'chat':
                # В ЛС и каналах отчёты недоступны и никаких сообщений не отправляется.
                return
            greeting = _user_greeting(message, event)
            remaining = services.cooldown.remaining_seconds(chat_id)
            if remaining:
                await event.message.answer(
                    f'Уважаемый {greeting}, подождите ещё {math.ceil(remaining / 60)} минут до следующего запроса.'
                )
                return
            services.repository.set_user_state(
                uid, 'summary_origin', {
                    'message_id': mid,
                    'chat_id': int(chat_id),
                    'greeting': greeting,
                }
            )
            await event.message.answer(
                f'Уважаемый {greeting}, какой отчёт сформировать?',
                attachments=summary_type_keyboard(),
            )
            return
        if text.startswith('/'):
            return

        if ctype != 'chat':
            return
        # Free trial is user-wide, never per chat.
        services.tokens.ensure_trial(uid)
        poll = extract_poll(message)
        if poll:
            try:
                services.repository.record_poll(
                    poll['poll_id'], chat_id, poll['question'], poll['data'], poll['total_votes']
                )
                poll_text = render_poll(poll)
                services.message.save_poll(
                    max_message_id=mid, chat_id=chat_id, user_id=uid,
                    user_name=name, text=poll_text, timestamp_ms=int(timestamp), reply_to=None,
                )
                logger.info('POLL_RECEIVED poll_id=%s chat_id=%s', poll['poll_id'], chat_id)
            except Exception:
                logger.exception('POLL_PARSE_FAILED')
            return
        if text:
            services.message.save_text(
                max_message_id=mid, chat_id=chat_id, user_id=uid,
                user_name=name, text=text, timestamp_ms=int(timestamp), reply_to=None,
            )

    @dp.message_callback()
    async def message_callback(event):
        callback = _get(event, 'callback')
        payload = _get(callback, 'payload', '') or ''
        message = _get(event, 'message')
        uid = _get(_get(event, 'from_user'), 'user_id') or _get(_get(message, 'sender'), 'user_id')
        if uid is None:
            return
        with contextlib.suppress(Exception):
            await event.answer()
        role = _role(services, uid)
        is_support = uid == services.settings_config.support_user_id
        # Удаляем только сообщения с шагами мастера отчёта в группах.
        # Остальные экраны и приветствие при добавлении бота не затрагиваем.
        if (
            _chat_type(message) == 'chat'
            and _is_report_navigation(payload)
            and not (payload == 'summary_menu' and _is_bot_added_greeting(message))
        ):
            await _delete_report_step(event)
        elif not payload.startswith(('support_reply:', 'ticket_reply:')):
            await _delete_callback_message(event)

        if payload == 'cabinet':
            await event.message.answer(
                services.cabinet.render(uid),
                attachments=cabinet_keyboard(services.subscription.is_active(uid), role),
            )
            return
        if payload == 'back':
            # Отмена незавершённого ввода.
            services.repository.clear_user_state(uid)
            if _chat_type(message) == 'chat':
                return
            await event.message.answer(
                GROUP_HELP_TEXT if _chat_type(message) == 'chat' else PRIVATE_HELP_TEXT,
                attachments=main_menu(services.subscription.is_active(uid), role, _chat_type(message)),
            )
            return
        if payload == 'summary_menu':
            if _chat_type(message) != 'chat':
                await event.message.answer('Отчёты доступны только в беседах.')
                return
            greeting = _user_greeting(message, event)
            chat_id = _get(_get(message, 'recipient'), 'chat_id')
            if chat_id is not None:
                services.repository.set_user_state(
                    uid, 'summary_origin', {'message_id': None, 'chat_id': int(chat_id), 'greeting': greeting}
                )
            await event.message.answer(
                f'Уважаемый {greeting}, какой отчёт сформировать?',
                attachments=summary_type_keyboard(),
            )
            return
        if payload == 'summary_cancel':
            if _chat_type(message) != 'chat':
                return
            services.repository.clear_user_state(uid)
            greeting = _user_greeting(message, event)
            await event.message.answer(
                f'Уважаемый {greeting}, какой отчёт сформировать?',
                attachments=summary_type_keyboard(),
            )
            return
        if payload == 'summary_type:general':
            if _chat_type(message) != 'chat':
                await event.message.answer('Отчёты доступны только в беседах.')
                return
            greeting = _user_greeting(message, event)
            origin = services.repository.get_user_state(uid)
            if not origin or origin[0] != 'summary_origin':
                chat_id = _get(_get(message, 'recipient'), 'chat_id')
                services.repository.set_user_state(
                    uid, 'summary_origin', {'message_id': None, 'chat_id': int(chat_id), 'greeting': greeting}
                )
            await event.message.answer(
                f'Уважаемый {greeting}, за какой срок сформировать общий отчёт?',
                attachments=summary_period_keyboard(),
            )
            return
        if payload in ('summary_type:people', 'summary_people'):
            chat_id = _get(_get(message, 'recipient'), 'chat_id')
            if _chat_type(message) != 'chat' or chat_id is None:
                await event.message.answer('Выбор участников доступен только в беседах.')
                return
            authors = services.repository.list_message_authors(chat_id)
            if not authors:
                await event.message.answer('Пока нет сообщений участников, по которым можно выбрать людей.')
                return
            origin = services.repository.get_user_state(uid)
            origin_id = origin[1].get('message_id') if origin and origin[0] == 'summary_origin' else None
            greeting = origin[1].get('greeting', _user_greeting(message, event)) if origin else _user_greeting(message, event)
            services.repository.set_user_state(
                uid, 'summary_people',
                {'chat_id': int(chat_id), 'selected': [], 'source_user_message_id': origin_id, 'greeting': greeting},
            )
            await event.message.answer(
                f'Уважаемый {greeting}, выберите одного или нескольких участников:',
                attachments=summary_people_keyboard(authors, []),
            )
            return
        if payload.startswith('summary_people_toggle:'):
            state = services.repository.get_user_state(uid)
            if not state or state[0] != 'summary_people':
                await event.message.answer('Пожалуйста, выберите участников заново.')
                return
            data = state[1]
            chat_id = int(data['chat_id'])
            if int(_get(_get(message, 'recipient'), 'chat_id') or chat_id) != chat_id:
                await event.message.answer('Пожалуйста, выберите участников заново.')
                return
            target = int(payload.split(':', 1)[1])
            selected = {int(x) for x in data.get('selected', [])}
            if target in selected:
                selected.remove(target)
            else:
                selected.add(target)
            data['selected'] = sorted(selected)
            services.repository.set_user_state(uid, 'summary_people', data)
            authors = services.repository.list_message_authors(chat_id)
            await event.message.answer(
                f'Уважаемый {data.get("greeting", _user_greeting(message, event))}, выберите одного или нескольких участников:',
                attachments=summary_people_keyboard(authors, data['selected']),
            )
            return
        if payload == 'summary_people_done':
            state = services.repository.get_user_state(uid)
            if not state or state[0] != 'summary_people' or not state[1].get('selected'):
                await event.message.answer('Выберите хотя бы одного участника.')
                return
            await event.message.answer(
                f'Уважаемый {state[1].get("greeting", _user_greeting(message, event))}, теперь выберите период:',
                attachments=summary_people_period_keyboard(),
            )
            return
        if payload.startswith('summary_people_period:'):
            if _chat_type(message) != 'chat':
                services.repository.clear_user_state(uid)
                await event.message.answer('Сегментация по людям доступна только в беседах.')
                return
            state = services.repository.get_user_state(uid)
            if not state or state[0] != 'summary_people' or not state[1].get('selected'):
                await event.message.answer('Пожалуйста, выберите участников заново.')
                return
            data = state[1]
            chat_id = int(data['chat_id'])
            selected = [int(x) for x in data['selected']]
            source_user_message_id = data.get('source_user_message_id')
            val = payload.split(':', 1)[1]
            services.repository.clear_user_state(uid)
            if val == 'all':
                start = datetime(1970, 1, 1, tzinfo=timezone.utc)
                end = datetime.now(timezone.utc)
                title = 'всё время'
            else:
                days = max(1, min(int(val), 3650))
                period = period_for_interval('custom', custom_days=days)
                start, end = period.start, period.end
                title = f'{days} ' + ('день' if days == 1 else 'дня' if 2 <= days <= 4 else 'дней')
            await generate_summary_custom(
                event, uid, chat_id, start, end, title,
                data.get('greeting', _user_greeting(message, event)),
                user_ids=selected, source_user_message_id=source_user_message_id,
            )
            return
        if payload == 'buy_subscription':
            await show_subscription(event, uid)
            return
        if payload.startswith('sub_tariff:'):
            tariff = payload.split(':', 1)[1]
            if role != 'user':
                await event.message.answer('Для административных ролей подписка бесконечная.')
                return
            order, link = await services.payment.create_order(uid, 'subscription', tariff, None)
            await send_payment(event, order, link)
            return
        if payload.startswith('summary_period:'):
            if _chat_type(message) != 'chat':
                await event.message.answer('Отчёты доступны только в беседах.')
                return
            val = payload.split(':', 1)[1]
            origin = services.repository.get_user_state(uid)
            origin_id = None
            if origin and origin[0] == 'summary_origin':
                origin_id = origin[1].get('message_id')
                greeting = origin[1].get('greeting', _user_greeting(message, event))
                services.repository.clear_user_state(uid)
            else:
                greeting = _user_greeting(message, event)
            chat_id = _get(_get(message, 'recipient'), 'chat_id')
            # Старое сообщение с выбором периода уже удалено; состояние
            # сохраняет имя и ник пользователя для итогового обращения.
            if val == 'all':
                period_start = datetime(1970, 1, 1, tzinfo=timezone.utc)
                await generate_summary_custom(
                    event, uid, chat_id, period_start, datetime.now(timezone.utc),
                    'всё время', greeting, source_user_message_id=origin_id,
                )
                return
            await generate_summary(
                event, uid, chat_id, max(1, min(int(val), 3650)),
                greeting, source_user_message_id=origin_id,
            )
            return
        if payload == 'admin_panel':
            if role != 'admin':
                await event.message.answer('Недостаточно прав.')
                return
            await event.message.answer('Административная панель', attachments=admin_panel_keyboard(role))
            return
        if payload == 'admin_chats':
            if role != 'admin':
                await event.message.answer('Недостаточно прав.')
                return
            rows = services.repository.list_group_chats()
            lines = [f'📋 Беседы с ботом: {len(rows)}']
            for i, row in enumerate(rows, 1):
                cid = row['chat_id']
                title = row['title']
                try:
                    chat = await event.bot.get_chat_by_id(int(cid))
                    title = _get(chat, 'title') or title
                    if title:
                        services.repository.update_chat_title(cid, title)
                except Exception:
                    logger.warning('ADMIN_CHAT_TITLE_LOOKUP_FAILED chat_id=%s', cid)
                lines.append(f'{i}. «{title or "Без названия"}» — id {cid}')
            if not rows:
                lines.append('Пока нет бесед, в которых есть бот.')
            await _send(event, '\n'.join(lines), services.settings_config, admin_panel_keyboard(role))
            return
        if payload.startswith('support_reply:'):
            await event.message.answer('Эта кнопка больше не действует. Создайте новое обращение через /помощь.')
            return
        if payload.startswith('ticket_reply:'):
            if _chat_type(message) in ('chat', 'channel'):
                return
            if not is_support and role != 'admin':
                await event.message.answer('Недостаточно прав.')
                return
            ticket_id = int(payload.split(':', 1)[1])
            ticket = services.repository.get_support_ticket(ticket_id)
            if not ticket or ticket['status'] != 'open':
                await event.message.answer('Обращение уже закрыто или не найдено.')
                return
            services.repository.set_user_state(uid, 'support_reply', {'ticket_id': ticket_id})
            await event.message.answer(
                f'Введите ответ по запросу №{ticket_id} ({ticket["user_name"]}, ID {ticket["user_id"]}).',
                attachments=back_keyboard(),
            )
            return
        if payload.startswith('ticket_close:'):
            if _chat_type(message) in ('chat', 'channel'):
                return
            if not is_support and role != 'admin':
                await event.message.answer('Недостаточно прав.')
                return
            ticket_id = int(payload.split(':', 1)[1])
            ticket = services.repository.get_support_ticket(ticket_id)
            if not ticket or not services.repository.close_support_ticket(ticket_id):
                await event.message.answer('Обращение уже закрыто или не найдено.')
                return
            try:
                await event.bot.send_message(
                    user_id=int(ticket['user_id']),
                    text=f'✅ Запрос №{ticket_id} закрыт поддержкой. Чтобы обратиться снова, отправьте /помощь.',
                )
            except Exception:
                logger.exception('SUPPORT_TICKET_CLOSE_NOTIFY_FAILED ticket_id=%s', ticket_id)
            await event.message.answer(f'Запрос №{ticket_id} закрыт.')
            return
        if payload.startswith('ticket_user_close:'):
            if _chat_type(message) in ('chat', 'channel'):
                return
            ticket_id = int(payload.split(':', 1)[1])
            ticket = services.repository.get_support_ticket(ticket_id)
            if not ticket or int(ticket['user_id']) != int(uid):
                await event.message.answer('Запрос не найден.')
                return
            if not services.repository.close_support_ticket(ticket_id):
                await event.message.answer('Запрос уже закрыт.')
                return
            services.repository.clear_user_state(uid)
            try:
                await event.bot.send_message(
                    user_id=int(services.settings_config.support_user_id),
                    text=f'ℹ️ Пользователь {ticket["user_name"]} (ID {uid}) закрыл запрос №{ticket_id}.',
                )
            except Exception:
                logger.exception('SUPPORT_TICKET_CLOSE_NOTIFY_FAILED ticket_id=%s', ticket_id)
            await event.message.answer(f'Запрос №{ticket_id} закрыт. Для нового обращения отправьте /помощь.')
            return
        if payload.startswith('payment_check:'):
            order_id = payload.split(':', 1)[1]
            try:
                status = await services.payment.verify_order(order_id, uid)
                if status is True:
                    end = services.subscription.active_until(uid)
                    text = '✅ Оплата подтверждена. Подписка активирована.'
                    if end:
                        text += f'\nПодписка действует до {end.astimezone(timezone.utc).strftime("%d.%m.%Y")}.'
                    await event.message.answer(text, attachments=cabinet_button_keyboard())
                elif status is None:
                    await event.message.answer(
                        '⏳ Оплата ещё не подтверждена. Если вы уже оплатили заказ, '
                        'подождите несколько секунд и нажмите «Проверить оплату» ещё раз.'
                    )
                else:
                    await event.message.answer('Оплата не подтверждена. Заказ ещё не оплачен или был отменён.')
            except Exception:
                logger.exception('PAYMENT_VERIFY_FAILED order_id=%s user_id=%s', order_id, uid)
                await event.message.answer('Не удалось проверить оплату. Попробуйте ещё раз через несколько секунд.')
            return
        if payload.startswith('retry:'):
            try:
                order, link = await services.payment.retry_order(payload.split(':', 1)[1], uid)
                await send_payment(event, order, link)
            except Exception as exc:
                await event.message.answer(str(exc))
            return
        if payload.startswith('cancel:'):
            await event.message.answer(
                'Заказ отменён.' if services.payment.cancel_order(payload.split(':', 1)[1], uid)
                else 'Заказ уже нельзя отменить.'
            )
            return

    @dp.message_removed()
    async def message_removed(event):
        # По актуальной схеме MAX message_removed содержит message_id/chat_id
        # непосредственно в Update. Оставляем fallback на вложенный объект,
        # чтобы старые версии maxapi не ломали синхронизацию удаления.
        message_id = _get(event, 'message_id')
        chat_id = _get(event, 'chat_id')
        if message_id is None:
            nested = _get(event, 'message') or _get(event, 'data') or {}
            message_id = _get(nested, 'message_id') or _get(nested, 'mid')
            chat_id = chat_id or _get(nested, 'chat_id')
        if message_id is None:
            logger.warning('MESSAGE_REMOVED_WITHOUT_ID event=%r', event)
            return
        removed = services.repository.delete_message(str(message_id))
        logger.info(
            'MESSAGE_REMOVED chat_id=%s message_id=%s deleted_from_context=%s',
            chat_id, message_id, removed,
        )

    @dp.message_edited()
    async def message_edited(event):
        message = _get(event, 'message')
        if not message:
            return
        body = _get(message, 'body') or {}
        mid = str(_get(body, 'mid') or _get(message, 'message_id') or '')
        if not mid:
            return
        chat_id = _get(_get(message, 'recipient'), 'chat_id')
        sender = _get(message, 'sender') or {}
        uid = _get(sender, 'user_id')
        first = _get(sender, 'first_name', '')
        last = _get(sender, 'last_name', '')
        name = ' '.join(x for x in (first, last) if x).strip() or str(uid or 'Пользователь')
        text = (_get(body, 'text') or '').strip()
        if chat_id is None:
            return
        if not text:
            removed = services.repository.delete_message(mid)
            logger.info(
                'MESSAGE_EDITED_TO_NON_TEXT chat_id=%s message_id=%s deleted_from_context=%s',
                chat_id, mid, removed,
            )
            return
        timestamp = _get(message, 'timestamp') or _get(event, 'timestamp')
        if timestamp is None:
            return
        updated = services.repository.update_message(
            MessageRecord(
                max_message_id=mid, chat_id=int(chat_id), user_id=uid, user_name=name,
                text=text, source='text',
                timestamp=datetime.fromtimestamp(int(timestamp) / 1000, tz=timezone.utc),
                reply_to=None,
            )
        )
        logger.info('MESSAGE_EDITED chat_id=%s message_id=%s updated_in_context=%s', chat_id, mid, updated)


def summary_people_period_keyboard():
    return kb([
        [btn('За 1 день', 'summary_people_period:1'), btn('За 3 дня', 'summary_people_period:3')],
        [btn('За 7 дней', 'summary_people_period:7'), btn('За 30 дней', 'summary_people_period:30')],
        [btn('↩️ Назад', 'summary_menu')],
    ])


def extract_poll(message):
    body = _get(message, 'body')
    attachments = _get(body, 'attachments', []) or []
    for a in attachments:
        if str(_get(a, 'type', '')).lower() != 'poll':
            continue
        p = _get(a, 'payload', {}) or {}
        pid = _get(p, 'poll_id') or _get(p, 'id') or _get(a, 'poll_id')
        q = _get(p, 'question') or _get(p, 'text') or 'Опрос'
        opts = _get(p, 'options') or _get(p, 'answers') or []
        normalized = []
        total = 0
        for i, o in enumerate(opts):
            txt = _get(o, 'text') or _get(o, 'title') or str(i + 1)
            votes = _get(o, 'votes') or _get(o, 'vote_count') or 0
            try:
                votes = int(votes)
            except Exception:
                votes = 0
            normalized.append({'text': txt, 'votes': votes})
            total += votes
        if pid is None:
            return None
        return {'poll_id': str(pid), 'question': q, 'data': {'options': normalized}, 'total_votes': total}
    return None


def render_poll(p):
    return (
        'Опрос: ' + p['question'] + '\n'
        + '\n'.join(f"{i + 1}. {x['text']} — {x['votes']} голосов" for i, x in enumerate(p['data']['options']))
        + f"\nВсего голосов: {p['total_votes']}"
    )
