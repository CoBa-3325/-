"""MAX event handlers implementing the complete user/admin flow."""
from __future__ import annotations

import logging
import math
import re

import aiohttp

from datetime import datetime, timezone

from maxapi import types

from bot.commands import HELP_TEXT
from bot.keyboards import *
from services.period_service import period_for_interval
from schemas.message import MessageRecord


logger = logging.getLogger(__name__)


def _get(obj, name, default=None):
    return (
        obj.get(name, default)
        if isinstance(obj, dict)
        else getattr(obj, name, default)
    )


def _command(text):
    if not text or not text.startswith("/"):
        return None, []

    parts = text.strip().split()

    return (
        parts[0].split("@", 1)[0].lower(),
        parts[1:],
    )


def _chat_type(message):
    recipient = _get(message, "recipient")

    return str(
        _get(recipient, "chat_type")
        or _get(recipient, "type")
        or "unknown"
    ).lower()


def _user(message, event=None):
    sender = (
        _get(message, "sender")
        or _get(event, "from_user")
    )

    uid = _get(sender, "user_id")

    first = _get(sender, "first_name", "")
    last = _get(sender, "last_name", "")

    name = (
        " ".join(
            x for x in (first, last)
            if x
        ).strip()
        or str(uid or "Пользователь")
    )

    return uid, name, first, last


def _message_meta(event):
    message = _get(event, "message")
    body = _get(message, "body")
    recipient = _get(message, "recipient")

    return (
        message,
        body,
        _get(recipient, "chat_id"),
        str(
            _get(body, "mid")
            or _get(message, "message_id")
            or ""
        ),
        _get(message, "timestamp")
        or _get(event, "timestamp"),
    )


def _inline(rows):
    return [
        types.ButtonsPayload(
            buttons=rows
        ).pack()
    ]


async def _send(
    event,
    text,
    settings,
    attachments=None,
):
    size = settings.max_outgoing_message_chars

    chunks = [
        text[i:i + size]
        for i in range(0, len(text), size)
    ] or [""]

    sent = []

    for i, chunk in enumerate(chunks):
        sent.append(
            await event.message.answer(
                chunk,
                attachments=(
                    attachments
                    if i == 0
                    else None
                ),
            )
        )

    return sent


async def _delete_max_message(
    settings,
    message_id,
):
    """Удаляет сообщение через официальный MAX API.

    В ЛС MAX разрешает боту удалять только собственные сообщения.
    В группе бот с правом удаления может удалить и сообщение пользователя.
    """

    if not message_id:
        return False

    url = "https://platform-api2.max.ru/messages"

    headers = {
        "Authorization": settings.max_bot_token
    }

    params = {
        "message_id": str(message_id)
    }

    try:
        timeout = aiohttp.ClientTimeout(
            total=10
        )

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.delete(
                url,
                params=params,
                headers=headers,
            ) as response:

                data = await response.json(
                    content_type=None
                )

                ok = (
                    bool(data.get("success"))
                    if isinstance(data, dict)
                    else response.status == 200
                )

                if not ok:
                    logger.warning(
                        "MAX_MESSAGE_DELETE_FAILED "
                        "message_id=%s status=%s response=%r",
                        message_id,
                        response.status,
                        data,
                    )

                return ok

    except Exception:
        logger.exception(
            "MAX_MESSAGE_DELETE_REQUEST_FAILED "
            "message_id=%s",
            message_id,
        )

        return False


async def _delete_callback_message(
    event,
    settings,
):
    """Удаляет старое сообщение бота, на кнопке которого пользователь нажал."""

    message = _get(event, "message")

    body = _get(message, "body") or {}

    message_id = str(
        _get(body, "mid")
        or _get(message, "message_id")
        or ""
    )

    if not message_id:
        return False

    return await _delete_max_message(
        settings,
        message_id,
    )


def _role(services, uid):
    return services.roles.get_role(uid)


async def _send_start_message(
    services,
    send,
    uid,
):
    """Единая реализация /start для команды и нативной кнопки Start в ЛС."""

    services.repository.upsert_user(uid)

    await send(
        HELP_TEXT,
        attachments=private_start_menu(
            services.subscription.is_active(uid),
            _role(services, uid),
        ),
    )


def register_handlers(dp, services):

    @dp.bot_started()
    async def bot_started(event):
        # bot_started приходит только для личного диалога с ботом.
        # Это именно нативная кнопка MAX «Начать».

        uid = (
            _get(
                _get(event, "user"),
                "user_id",
            )
            or getattr(event, "user_id", None)
        )

        chat_id = _get(
            event,
            "chat_id",
        )

        if uid is None or chat_id is None:
            logger.warning(
                "BOT_STARTED_INVALID_EVENT event=%r",
                event,
            )

            return

        services.repository.upsert_user(uid)

        services.repository.upsert_chat(
            chat_id,
            "dialog",
        )

        await _send_start_message(
            services,
            lambda text, attachments=None:
                event.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    attachments=attachments,
                ),
            uid,
        )


    @dp.message_created()
    async def message_created(event):
        (
            message,
            body,
            chat_id,
            mid,
            timestamp,
        ) = _message_meta(event)

        if (
            chat_id is None
            or not mid
            or timestamp is None
        ):
            return

        uid, name, first, last = _user(
            message,
            event,
        )

        if uid is None:
            return

        sender = _get(
            message,
            "sender",
        )

        if _get(
            sender,
            "is_bot",
            False,
        ):
            return

        ctype = _chat_type(message)

        services.repository.upsert_user(
            uid,
            first,
            last,
        )

        services.repository.upsert_chat(
            chat_id,
            ctype,
        )

        text = (
            _get(body, "text")
            or ""
        ).strip()

        command, args = _command(text)

        # Persistent input flows survive bot restarts.
        state = services.repository.get_user_state(uid)

        if state and not text.startswith("/"):
            if await handle_state_input(
                event,
                uid,
                text,
                state,
            ):
                return

        if command in (
            "/start",
            "/help",
        ):
            menu = (
                main_menu
                if ctype == "chat"
                else private_start_menu
            )

            await _send(
                event,
                HELP_TEXT,
                services.settings_config,
                menu(
                    services.subscription.is_active(uid),
                    _role(services, uid),
                ),
            )

            return

        if command in (
            "/cabinet",
            "/buy",
        ):
            await _send(
                event,
                services.cabinet.render(uid),
                services.settings_config,
                cabinet_keyboard(
                    services.subscription.is_active(uid),
                    _role(services, uid),
                ),
            )

            return

        if command == "/buy_tokens":
            await event.message.answer(
                "Осталось токенов: %s"
                % services.tokens.balance(uid),
                attachments=token_buy_keyboard(
                    services.settings_config.token_price_10
                ),
            )

            return

        if command == "/buy_subscription":
            await show_subscription(
                event,
                uid,
            )

            return

        if command == "/summary":
            if ctype != "chat":
                await event.message.answer(
                    "Команда /summary доступна только в беседах."
                )

                return

            # Само сообщение пользователя /summary удаляем
            # только после успешного формирования сводки.
            # До этого оно остаётся в чате.

            services.repository.set_user_state(
                uid,
                "summary_origin",
                {
                    "message_id": mid,
                    "chat_id": int(chat_id),
                },
            )

            await event.message.answer(
                "Какую сводку сформировать?",
                attachments=summary_type_keyboard(),
            )

            return

        if command == "/settings":
            # Explicitly removed by the specification.
            return

        if text.startswith("/"):
            return

        if ctype != "chat":
            return

        # Free trial is user-wide, never per chat.
        services.tokens.ensure_trial(uid)

        poll = extract_poll(message)

        if poll:
            try:
                services.repository.record_poll(
                    poll["poll_id"],
                    chat_id,
                    poll["question"],
                    poll["data"],
                    poll["total_votes"],
                )

                poll_text = render_poll(poll)

                services.message.save_poll(
                    max_message_id=mid,
                    chat_id=chat_id,
                    user_id=uid,
                    user_name=name,
                    text=poll_text,
                    timestamp_ms=int(timestamp),
                    reply_to=None,
                )

                logger.info(
                    "POLL_RECEIVED poll_id=%s chat_id=%s",
                    poll["poll_id"],
                    chat_id,
                )

            except Exception:
                logger.exception(
                    "POLL_PARSE_FAILED"
                )

            return

        if text:
            services.message.save_text(
                max_message_id=mid,
                chat_id=chat_id,
                user_id=uid,
                user_name=name,
                text=text,
                timestamp_ms=int(timestamp),
                reply_to=None,
            )


    async def show_subscription(
        event,
        uid,
    ):
        if _role(services, uid) != "user":
            await event.message.answer(
                "Для административных ролей подписка бесконечная."
            )

            return

        end = services.subscription.active_until(uid)

        now = datetime.now(
            timezone.utc
        )

        if end:
            txt = (
                "Дата окончания подписки "
                f"{end.astimezone(timezone.utc).strftime('%d.%m.%Y')}\n"
                "Дней до конца "
                f"{max(0, math.ceil((end - now).total_seconds() / 86400))}"
            )

        else:
            txt = (
                "У вас не оформлена подписка\n"
                "Дней до конца 0"
            )

        await event.message.answer(
            txt,
            attachments=subscription_keyboard(
                services.settings_config.subscription_price_1_month,
                services.settings_config.subscription_price_3_months,
                services.settings_config.subscription_price_6_months,
            ),
        )


    async def generate_summary(
        event,
        uid,
        chat_id,
        days,
        source_user_message_id=None,
    ):
        remaining = services.cooldown.remaining_seconds(
            chat_id
        )

        if remaining:
            await event.message.answer(
                "До следующего запроса осталось "
                f"{math.ceil(remaining / 60)} минут"
            )

            return

        if not services.cooldown.acquire(chat_id):
            remaining = services.cooldown.remaining_seconds(
                chat_id
            )

            await event.message.answer(
                (
                    f"До следующего запроса осталось "
                    f"{math.ceil(remaining / 60)} минут"
                    if remaining
                    else "В этом чате уже выполняется запрос."
                )
            )

            return

        period = period_for_interval(
            "custom",
            custom_days=days,
        )

        rows = services.repository.get_messages(
            chat_id,
            period.start,
            period.end,
        )

        if not rows:
            services.cooldown.release(chat_id)

            await event.message.answer(
                "За выбранный период сообщений нет."
            )

            return

        unlimited = services.subscription.is_active(
            uid
        )

        operation = None

        if not unlimited:
            operation = services.tokens.deduct_for_llm(
                uid,
                chat_id,
            )

            if operation is None:
                services.cooldown.release(chat_id)

                await event.message.answer(
                    "Токены закончились."
                )

                return

        llm_succeeded = False

        try:
            result = await services.summary.generate(
                chat_id=chat_id,
                start=period.start,
                end=period.end,
            )

            llm_succeeded = True

            services.cooldown.activate(
                chat_id
            )

            services.summary.save_run(
                chat_id,
                period.start,
                period.end,
                result,
            )

            await _send(
                event,
                (
                    f"📊 Сводка за {days} "
                    + (
                        "день"
                        if days == 1
                        else (
                            "дня"
                            if 2 <= days <= 4
                            else "дней"
                        )
                    )
                    + "\n\n"
                    + result
                ),
                services.settings_config,
            )

            # После успешного формирования сводки
            # удаляем именно сообщение пользователя /summary.
            if source_user_message_id:
                await _delete_max_message(
                    services.settings_config,
                    source_user_message_id,
                )

        except Exception:
            if operation and not llm_succeeded:
                services.tokens.refund(
                    uid,
                    chat_id,
                    operation,
                )

            services.cooldown.release(
                chat_id
            )

            logger.exception(
                "LLM_REQUEST_FAILED chat_id=%s user_id=%s",
                chat_id,
                uid,
            )

            await event.message.answer(
                "Не удалось сформировать сводку. "
                "Токен возвращён, если он был списан."
            )


    @dp.message_removed()
    async def message_removed(event):
        # По актуальной схеме MAX message_removed содержит
        # message_id/chat_id непосредственно в Update.
        #
        # Оставляем fallback на вложенный объект,
        # чтобы старые версии maxapi не ломали
        # синхронизацию удаления.

        message_id = _get(
            event,
            "message_id",
        )

        chat_id = _get(
            event,
            "chat_id",
        )

        if message_id is None:
            nested = (
                _get(event, "message")
                or _get(event, "data")
                or {}
            )

            message_id = (
                _get(
                    nested,
                    "message_id",
                )
                or _get(
                    nested,
                    "mid",
                )
            )

            chat_id = (
                chat_id
                or _get(
                    nested,
                    "chat_id",
                )
            )

        if message_id is None:
            logger.warning(
                "MESSAGE_REMOVED_WITHOUT_ID event=%r",
                event,
            )

            return

        removed = services.repository.delete_message(
            str(message_id)
        )

        logger.info(
            "MESSAGE_REMOVED chat_id=%s message_id=%s "
            "deleted_from_context=%s",
            chat_id,
            message_id,
            removed,
        )


    @dp.message_edited()
    async def message_edited(event):
        message = _get(
            event,
            "message",
        )

        if not message:
            return

        body = (
            _get(message, "body")
            or {}
        )

        mid = str(
            _get(body, "mid")
            or _get(message, "message_id")
            or ""
        )

        if not mid:
            return

        chat_id = _get(
            _get(message, "recipient"),
            "chat_id",
        )

        sender = (
            _get(message, "sender")
            or {}
        )

        uid = _get(
            sender,
            "user_id",
        )

        first = _get(
            sender,
            "first_name",
            "",
        )

        last = _get(
            sender,
            "last_name",
            "",
        )

        name = (
            " ".join(
                x for x in (first, last)
                if x
            ).strip()
            or str(
                uid
                or "Пользователь"
            )
        )

        text = (
            _get(body, "text")
            or ""
        ).strip()

        if chat_id is None:
            return

        if not text:
            removed = services.repository.delete_message(
                mid
            )

            logger.info(
                "MESSAGE_EDITED_TO_NON_TEXT "
                "chat_id=%s message_id=%s "
                "deleted_from_context=%s",
                chat_id,
                mid,
                removed,
            )

            return

        timestamp = (
            _get(message, "timestamp")
            or _get(event, "timestamp")
        )

        if timestamp is None:
            return

        updated = services.repository.update_message(
            MessageRecord(
                max_message_id=mid,
                chat_id=int(chat_id),
                user_id=uid,
                user_name=name,
                text=text,
                source="text",
                timestamp=datetime.fromtimestamp(
                    int(timestamp) / 1000,
                    tz=timezone.utc,
                ),
                reply_to=None,
            )
        )

        logger.info(
            "MESSAGE_EDITED chat_id=%s message_id=%s "
            "updated_in_context=%s",
            chat_id,
            mid,
            updated,
        )


def summary_people_period_keyboard():
    return kb([
        [
            btn(
                "За 1 день",
                "summary_people_period:1",
            ),
            btn(
                "За 3 дня",
                "summary_people_period:3",
            ),
        ],
        [
            btn(
                "За 7 дней",
                "summary_people_period:7",
            ),
            btn(
                "За всё время",
                "summary_people_period:all",
            ),
        ],
        [
            btn(
                "↩️ Назад",
                "summary_menu",
            )
        ],
    ])


def extract_poll(message):
    body = _get(
        message,
        "body",
    )

    attachments = (
        _get(
            body,
            "attachments",
            [],
        )
        or []
    )

    for attachment in attachments:
        if str(
            _get(
                attachment,
                "type",
                "",
            )
        ).lower() != "poll":
            continue

        payload = (
            _get(
                attachment,
                "payload",
                {},
            )
            or {}
        )

        poll_id = (
            _get(payload, "poll_id")
            or _get(payload, "id")
            or _get(attachment, "poll_id")
        )

        question = (
            _get(payload, "question")
            or _get(payload, "text")
            or "Опрос"
        )

        options = (
            _get(payload, "options")
            or _get(payload, "answers")
            or []
        )

        normalized = []
        total = 0

        for i, option in enumerate(options):
            text = (
                _get(option, "text")
                or _get(option, "title")
                or str(i + 1)
            )

            votes = (
                _get(option, "votes")
                or _get(option, "vote_count")
                or 0
            )

            try:
                votes = int(votes)

            except Exception:
                votes = 0

            normalized.append(
                {
                    "text": text,
                    "votes": votes,
                }
            )

            total += votes

        if poll_id is None:
            return None

        return {
            "poll_id": str(poll_id),
            "question": question,
            "data": {
                "options": normalized
            },
            "total_votes": total,
        }

    return None


def render_poll(p):
    return (
        "Опрос: "
        + p["question"]
        + "\n"
        + "\n".join(
            f"{i + 1}. {x['text']} — {x['votes']} голосов"
            for i, x in enumerate(
                p["data"]["options"]
            )
        )
        + f"\nВсего голосов: {p['total_votes']}"
    )