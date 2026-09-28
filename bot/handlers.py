"""MAX event handlers implementing the complete user/admin flow."""
from __future__ import annotations
import logging, math, re
import aiohttp
from datetime import datetime, timezone
from maxapi import types
from  bot.commands import GROUP_HELP_TEXT, PRIVATE_HELP_TEXT
from  bot.keyboards import *
from  services.period_service import period_for_interval
from schemas.message import MessageRecord
logger=logging.getLogger(__name__)

def _get(obj,name,default=None): return obj.get(name,default) if isinstance(obj,dict) else getattr(obj,name,default)
def _command(text):
    if not text or not text.startswith('/'): return None,[]
    parts=text.strip().split(); return parts[0].split('@',1)[0].lower(),parts[1:]
def _chat_type(message):
    r=_get(message,'recipient'); return str(_get(r,'chat_type') or _get(r,'type') or 'unknown').lower()
def _user(message,event=None):
    sender=_get(message,'sender') or _get(event,'from_user'); uid=_get(sender,'user_id'); first=_get(sender,'first_name','');last=_get(sender,'last_name','');name=' '.join(x for x in (first,last) if x).strip() or str(uid or 'Пользователь');return uid,name,first,last
def _message_meta(event):
    m=_get(event,'message');b=_get(m,'body');r=_get(m,'recipient');return m,b,_get(r,'chat_id'),str(_get(b,'mid') or _get(m,'message_id') or ''),_get(m,'timestamp') or _get(event,'timestamp')
def _inline(rows): return [types.ButtonsPayload(buttons=rows).pack()]
async def _send(event,text,settings,attachments=None):
    size=settings.max_outgoing_message_chars
    chunks=[text[i:i+size] for i in range(0,len(text),size)] or ['']
    sent=[]
    for i,c in enumerate(chunks):
        sent.append(await event.message.answer(c,attachments=attachments if i==0 else None))
    return sent

async def _delete_max_message(settings,message_id):
    """Удаляет сообщение через официальный MAX API.

    В ЛС MAX разрешает боту удалять только собственные сообщения.
    В группе бот с правом удаления может удалить и сообщение пользователя.
    """
    if not message_id:
        return False
    url='https://platform-api2.max.ru/messages'
    headers={'Authorization':settings.max_bot_token}
    params={'message_id':str(message_id)}
    try:
        timeout=aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.delete(url,params=params,headers=headers) as response:
                data=await response.json(content_type=None)
                ok=bool(data.get('success')) if isinstance(data,dict) else response.status==200
                if not ok:
                    logger.warning('MAX_MESSAGE_DELETE_FAILED message_id=%s status=%s response=%r',message_id,response.status,data)
                return ok
    except Exception:
        logger.exception('MAX_MESSAGE_DELETE_REQUEST_FAILED message_id=%s',message_id)
        return False

async def _delete_callback_message(event,settings):
    """Удаляет старое сообщение бота только в ЛС.

    В группах сообщения бота при навигации не удаляются.
    """
    message=_get(event,'message')
    if _chat_type(message) == 'chat':
        return False
    body=_get(message,'body') or {}
    message_id=str(_get(body,'mid') or _get(message,'message_id') or '')
    if not message_id:
        return False
    return await _delete_max_message(settings,message_id)

def _role(services,uid): return services.roles.get_role(uid)

async def _send_start_message(services, send, uid):
    """Единая реализация /start для команды и нативной кнопки Start в ЛС."""
    services.repository.upsert_user(uid)
    await send(
        PRIVATE_HELP_TEXT,
        attachments=private_start_menu(
            services.subscription.is_active(uid),
            _role(services,uid),
        ),
    )

def register_handlers(dp,services):
    @dp.bot_started()
    async def bot_started(event):
        # bot_started приходит только для личного диалога с ботом.
        # Это именно нативная кнопка MAX «Начать», поэтому отдельную
        # inline-кнопку в ЛС создавать не нужно.
        uid=_get(_get(event,'user'),'user_id') or getattr(event,'user_id',None)
        chat_id=_get(event,'chat_id')
        if uid is None or chat_id is None:
            logger.warning('BOT_STARTED_INVALID_EVENT event=%r',event)
            return
        services.repository.upsert_user(uid)
        services.repository.upsert_chat(chat_id,'dialog')
        await _send_start_message(
            services,
            lambda text,attachments=None: event.bot.send_message(
                chat_id=chat_id, text=text, attachments=attachments
            ),
            uid,
        )

    @dp.message_created()
    async def message_created(event):
        message,body,chat_id,mid,timestamp=_message_meta(event)
        if chat_id is None or not mid or timestamp is None:return
        uid,name,first,last=_user(message,event)
        if uid is None:return
        sender=_get(message,'sender')
        if _get(sender,'is_bot',False):return
        ctype=_chat_type(message);services.repository.upsert_user(uid,first,last);services.repository.upsert_chat(chat_id,ctype)
        text=(_get(body,'text') or '').strip();command,args=_command(text)

        # Persistent input flows survive bot restarts.
        state=services.repository.get_user_state(uid)
        if state and not text.startswith('/'):
            if await handle_state_input(event,uid,text,state): return

        if command in ('/start','/help'):
            menu = (main_menu if ctype=='chat' else private_start_menu)
            help_text = GROUP_HELP_TEXT if ctype=='chat' else PRIVATE_HELP_TEXT
            await _send(event,help_text,services.settings_config,menu(services.subscription.is_active(uid),_role(services,uid)));return
        if command in ('/cabinet','/buy'):
            await _send(event,services.cabinet.render(uid),services.settings_config,cabinet_keyboard(services.subscription.is_active(uid),_role(services,uid)));return
        if command=='/buy_tokens':
            await event.message.answer('Осталось токенов: %s'%services.tokens.balance(uid),attachments=token_buy_keyboard(services.settings_config.token_price_10));return
        if command=='/buy_subscription':
            await show_subscription(event,uid);return
        if command=='/summary':
            if ctype!='chat':
                # В ЛС команда не выполняется и не вызывает никаких
                # сообщений о групповой функциональности.
                return
            # Само сообщение пользователя /summary удаляем только после
            # успешного формирования сводки. До этого оно остаётся в чате.
            services.repository.set_user_state(uid,'summary_origin',{'message_id':mid,'chat_id':int(chat_id)})
            await event.message.answer(
                'Какую сводку сформировать?',
                attachments=summary_type_keyboard()
            )
            return
        if command=='/settings':
            # Explicitly removed by the specification.
            return
        if text.startswith('/'):return

        if ctype!='chat':return
        # Free trial is user-wide, never per chat.
        services.tokens.ensure_trial(uid)
        poll=extract_poll(message)
        if poll:
            try:
                services.repository.record_poll(poll['poll_id'],chat_id,poll['question'],poll['data'],poll['total_votes'])
                poll_text=render_poll(poll)
                services.message.save_poll(max_message_id=mid,chat_id=chat_id,user_id=uid,user_name=name,text=poll_text,timestamp_ms=int(timestamp),reply_to=None)
                logger.info('POLL_RECEIVED poll_id=%s chat_id=%s',poll['poll_id'],chat_id)
            except Exception:logger.exception('POLL_PARSE_FAILED')
            return
        if text:
            services.message.save_text(max_message_id=mid,chat_id=chat_id,user_id=uid,user_name=name,text=text,timestamp_ms=int(timestamp),reply_to=None)

    async def show_subscription(event,uid):
        if _role(services,uid)!='user':
            await event.message.answer('Для административных ролей подписка бесконечная.');return
        end=services.subscription.active_until(uid);now=datetime.now(timezone.utc)
        if end: txt=f'Дата окончания подписки {end.astimezone(timezone.utc).strftime("%d.%m.%Y")}\nДней до конца {max(0,math.ceil((end-now).total_seconds()/86400))}'
        else:txt='У вас не оформлена подписка\nДней до конца 0'
        await event.message.answer(txt,attachments=subscription_keyboard(services.settings_config.subscription_price_1_month,services.settings_config.subscription_price_3_months,services.settings_config.subscription_price_6_months))

    async def generate_summary(event,uid,chat_id,days,source_user_message_id=None):
        remaining=services.cooldown.remaining_seconds(chat_id)
        if remaining:
            await event.message.answer(f'До следующего запроса осталось {math.ceil(remaining/60)} минут');return
        if not services.cooldown.acquire(chat_id):
            remaining=services.cooldown.remaining_seconds(chat_id);await event.message.answer(f'До следующего запроса осталось {math.ceil(remaining/60)} минут' if remaining else 'В этом чате уже выполняется запрос.');return
        period=period_for_interval('custom',custom_days=days)
        rows=services.repository.get_messages(chat_id,period.start,period.end)
        if not rows:
            services.cooldown.release(chat_id);await event.message.answer('За выбранный период сообщений нет.');return
        unlimited=services.subscription.is_active(uid)
        operation=None
        if not unlimited:
            operation=services.tokens.deduct_for_llm(uid,chat_id)
            if operation is None:
                services.cooldown.release(chat_id);await event.message.answer('Токены закончились.');return
        llm_succeeded=False
        try:
            result=await services.summary.generate(chat_id=chat_id,start=period.start,end=period.end)
            llm_succeeded=True
            services.cooldown.activate(chat_id)
            services.summary.save_run(chat_id,period.start,period.end,result)
            await _send(event,f'📊 Сводка за {days} '+('день' if days==1 else 'дня' if 2<=days<=4 else 'дней')+'\n\n'+result,services.settings_config)
        except Exception:
            if operation and not llm_succeeded:services.tokens.refund(uid,chat_id,operation)
            services.cooldown.release(chat_id)
            logger.exception('LLM_REQUEST_FAILED chat_id=%s user_id=%s',chat_id,uid)
            await event.message.answer('Не удалось сформировать сводку. Токен возвращён, если он был списан.')

    async def handle_state_input(event,uid,text,state):
        name,data=state
        if name=='redeem_promo':
            p=services.promotion.redeem(uid,text);services.repository.clear_user_state(uid)
            await event.message.answer('Промокод успешно активирован.' if p else 'Промокод недействителен или уже использован',attachments=main_menu(services.subscription.is_active(uid),_role(services,uid)));return True
        if name=='promo_token_amount':
            if not text.isdigit() or int(text)<=0:
                await event.message.answer('Введите только число');return True
            data['token_amount']=int(text);services.repository.set_user_state(uid,'promo_code',data);await event.message.answer('Введите промокод');return True
        if name=='promo_code':
            code=''.join(text.split()).upper()
            if not code:await event.message.answer('Промокод не может быть пустым');return True
            data['code']=code;services.repository.set_user_state(uid,'promo_usage',data);await event.message.answer('Выберите режим использования:',attachments=promo_usage_keyboard());return True
        if name=='promo_max_uses':
            if not text.isdigit() or int(text)<=0:await event.message.answer('Введите только положительное число');return True
            data['max_uses']=int(text)
            await create_promo_from_state(event,uid,data);return True
        return False

    async def create_promo_from_state(event,uid,data):
        try:
            services.promotion.create(uid,code=data['code'],usage_type=data['usage_type'],max_uses=data.get('max_uses'),reward_type=data['reward_type'],token_amount=data.get('token_amount',0),subscription_months=data.get('subscription_months',0),unlimited=data.get('unlimited',False),admin_role=data.get('admin_role'))
            services.repository.clear_user_state(uid);logger.info('PROMO_CREATED actor=%s reward=%s',uid,data['reward_type']);await event.message.answer('Промокод успешно создан.',attachments=admin_panel_keyboard(_role(services,uid)))
        except Exception as exc:
            if 'UNIQUE' in str(exc).upper():await event.message.answer('Такой промокод уже существует. Введите другой промокод.')
            else:await event.message.answer(str(exc))

    @dp.message_callback()
    async def message_callback(event):
        callback=_get(event,'callback');payload=_get(callback,'payload','') or '';message=_get(event,'message');uid=_get(_get(event,'from_user'),'user_id') or _get(_get(message,'sender'),'user_id')
        if uid is None:return
        try:await event.answer()
        except Exception:pass
        role=_role(services,uid)
        # Любой переход по inline-кнопке заменяет старое СООБЩЕНИЕ БОТА новым.
        # Пользовательские сообщения здесь не удаляются.
        await _delete_callback_message(event,services.settings_config)

        if payload=='cabinet':
            await event.message.answer(services.cabinet.render(uid),attachments=cabinet_keyboard(services.subscription.is_active(uid),role));return
        if payload=='back':
            await event.message.answer('Главное меню:',attachments=main_menu(services.subscription.is_active(uid),role));return
        if payload=='summary_menu':
            if _chat_type(message)!='chat':await event.message.answer('Команда /summary доступна только в беседах.');return
            await event.message.answer('Какую сводку сформировать?',attachments=summary_type_keyboard());return
        if payload=='summary_type:general':
            if _chat_type(message)!='chat':await event.message.answer('Команда /summary доступна только в беседах.');return
            await event.message.answer('За какой срок сформировать общую сводку?',attachments=summary_period_keyboard());return
        if payload in ('summary_type:people','summary_people'):
            chat_id=_get(_get(message,'recipient'),'chat_id')
            if _chat_type(message)!='chat' or chat_id is None:
                await event.message.answer('Сегментация по людям доступна только в беседах.');return
            authors=services.repository.list_message_authors(chat_id)
            if not authors:
                await event.message.answer('Пока нет сохранённых сообщений участников, по которым можно сделать сегментацию.');return
            origin=services.repository.get_user_state(uid)
            origin_id=origin[1].get('message_id') if origin and origin[0]=='summary_origin' else None
            services.repository.set_user_state(uid,'summary_people',{'chat_id':int(chat_id),'selected':[],'source_user_message_id':origin_id})
            await event.message.answer('Выберите одного или нескольких участников:',attachments=summary_people_keyboard(authors,[]));return
        if payload.startswith('summary_people_toggle:'):
            state=services.repository.get_user_state(uid)
            if not state or state[0]!='summary_people':
                await event.message.answer('Сессия выбора участников истекла. Откройте сегментацию заново.');return
            data=state[1]; chat_id=int(data['chat_id'])
            if int(_get(_get(message,'recipient'),'chat_id') or chat_id)!=chat_id:
                await event.message.answer('Выбор участников относится к другому чату.');return
            target=int(payload.split(':',1)[1]); selected={int(x) for x in data.get('selected',[])}
            if target in selected:selected.remove(target)
            else:selected.add(target)
            data['selected']=sorted(selected);services.repository.set_user_state(uid,'summary_people',data)
            authors=services.repository.list_message_authors(chat_id)
            await event.message.answer('Выберите одного или нескольких участников:',attachments=summary_people_keyboard(authors,data['selected']));return
        if payload=='summary_people_done':
            state=services.repository.get_user_state(uid)
            if not state or state[0]!='summary_people' or not state[1].get('selected'):
                await event.message.answer('Выберите хотя бы одного участника.');return
            await event.message.answer('Теперь выберите период:',attachments=summary_people_period_keyboard());return
        if payload.startswith('summary_people_period:'):
            state=services.repository.get_user_state(uid)
            if not state or state[0]!='summary_people' or not state[1].get('selected'):
                await event.message.answer('Сессия выбора участников истекла. Откройте сегментацию заново.');return
            data=state[1];chat_id=int(data['chat_id']);selected=[int(x) for x in data['selected']]
            val=payload.split(':',1)[1]
            services.repository.clear_user_state(uid)
            if val=='all':
                start=datetime(1970,1,1,tzinfo=timezone.utc);end=datetime.now(timezone.utc);title='всё время'
            else:
                days=max(1,min(int(val),3650));period=period_for_interval('custom',custom_days=days);start,end=period.start,period.end
                title=f'{days} '+('день' if days==1 else 'дня' if 2<=days<=4 else 'дней')
            await generate_summary_custom(event,uid,chat_id,start,end,title,user_ids=selected,source_user_message_id=data.get('source_user_message_id'));return
        if payload=='redeem_promo':
            services.repository.set_user_state(uid,'redeem_promo',{});await event.message.answer('Введите промокод');return
        if payload=='buy_tokens':await event.message.answer('Осталось токенов: %s'%services.tokens.balance(uid),attachments=token_buy_keyboard(services.settings_config.token_price_10));return
        if payload=='buy_subscription':await show_subscription(event,uid);return
        if payload=='token_buy:10':
            order,link=await services.payment.create_order(uid,'tokens','10',None);await send_payment(event,order,link);return
        if payload.startswith('sub_tariff:'):
            tariff=payload.split(':',1)[1]
            if role!='user':await event.message.answer('Для административных ролей подписка бесконечная.');return
            order,link=await services.payment.create_order(uid,'subscription',tariff,None);await send_payment(event,order,link);return
        if payload.startswith('summary_period:'):
            val=payload.split(':',1)[1]
            origin=services.repository.get_user_state(uid)
            origin_id=None
            if origin and origin[0]=='summary_origin':
                origin_id=origin[1].get('message_id')
                services.repository.clear_user_state(uid)
            chat_id=_get(_get(message,'recipient'),'chat_id')
            if val=='all':
                period_start=datetime(1970,1,1,tzinfo=timezone.utc)
                await generate_summary_custom(event,uid,chat_id,period_start,datetime.now(timezone.utc),'всё время',source_user_message_id=origin_id);return
            await generate_summary(event,uid,chat_id,max(1,min(int(val),3650)),source_user_message_id=origin_id);return
        if payload=='admin_panel':
            if role not in ('admin','creator'):await event.message.answer('Недостаточно прав.');return
            await event.message.answer('Административная панель',attachments=admin_panel_keyboard(role));return
        if payload=='promo_menu':
            if role not in ('admin','creator'):await event.message.answer('Недостаточно прав.');return
            await event.message.answer('Страница генерации промокодов',attachments=promo_reward_keyboard(role));return
        if payload.startswith('promo_reward:'):
            if role not in ('admin','creator'):await event.message.answer('Недостаточно прав.');return
            kind=payload.split(':',1)[1]
            if kind=='subscription':await event.message.answer('Выберите срок:',attachments=promo_subscription_keyboard());return
            if kind=='tokens':services.repository.set_user_state(uid,'promo_token_amount',{'reward_type':'tokens'});await event.message.answer('Введите количество токенов');return
            if kind in ('admin','creator'):
                if not services.roles.can_create_role_promo(uid,kind):await event.message.answer('Недостаточно прав.');return
                services.repository.set_user_state(uid,'promo_code',{'reward_type':'role','admin_role':kind});await event.message.answer('Введите промокод');return
        if payload.startswith('promo_sub:'):
            value=payload.split(':',1)[1];data={'reward_type':'subscription'}
            if value=='unlimited':data['unlimited']=True
            else:data['subscription_months']=int(value)
            services.repository.set_user_state(uid,'promo_code',data);await event.message.answer('Введите промокод');return
        if payload.startswith('promo_usage:'):
            state=services.repository.get_user_state(uid)
            if not state or state[0]!='promo_usage':await event.message.answer('Сессия создания промокода не найдена.');return
            data=state[1];data['usage_type']=payload.split(':',1)[1]
            if data['usage_type']=='limited':services.repository.set_user_state(uid,'promo_max_uses',data);await event.message.answer('Введите максимальное число использований');return
            await create_promo_from_state(event,uid,data);return
        if payload=='creators':
            if not services.roles.can_view_creators(uid):await event.message.answer('Недостаточно прав.');return
            await event.message.answer('👥 Создатели',attachments=user_list_keyboard(services.repository.list_users_by_role('creator'),'noop'));return
        if payload=='admins':
            if role!='creator':await event.message.answer('Недостаточно прав.');return
            await event.message.answer('Выберите администратора:',attachments=user_list_keyboard(services.repository.list_users_by_role('admin'),'remove_admin'));return
        if payload.startswith('remove_admin:'):
            target=int(payload.split(':',1)[1])
            if not services.roles.can_manage(uid,target):await event.message.answer('Недостаточно прав или роль уже изменилась.');return
            name=services.repository.user_display_name(target);await event.message.answer(f'Вы точно хотите удалить администратора «{name}»?',attachments=confirm_keyboard(f'confirm_remove_admin:{target}','admins'));return
        if payload.startswith('confirm_remove_admin:'):
            target=int(payload.split(':',1)[1]);
            if not services.roles.can_manage(uid,target):await event.message.answer('Нельзя снять этого пользователя: роль уже изменилась или недостаточно прав.');return
            if services.roles.set_role(uid,target,'user'):await event.message.answer(f'Администратор «{services.repository.user_display_name(target)}» снят с роли администратора.',attachments=admin_panel_keyboard('creator'))
            else:await event.message.answer('Не удалось изменить роль: пользователь уже был изменён.')
            return
        if payload=='creator_exit':
            if role!='creator':await event.message.answer('Недостаточно прав.');return
            await event.message.answer('Вы точно хотите выйти из роли создателя? После подтверждения ваша роль будет изменена на обычного пользователя.',attachments=confirm_keyboard('confirm_creator_exit','admin_panel'));return
        if payload=='confirm_creator_exit':
            if services.repository.set_role_if_current(uid,'creator','user'):await event.message.answer('Вы больше не являетесь создателем.',attachments=main_menu(services.subscription.is_active(uid),'user'))
            else:await event.message.answer('Роль уже изменилась.')
            return
        if payload.startswith('payment_check:'):
            order_id=payload.split(':',1)[1]
            try:
                status=await services.payment.verify_order(order_id,uid)
                order=services.repository.get_order(order_id)
                if status is True:
                    if order and order['product_type']=='tokens':
                        await event.message.answer(f'✅ Оплата подтверждена. Начислено токенов: {order["token_amount"]}.\nБаланс: {services.tokens.balance(uid)}')
                    else:
                        end=services.subscription.active_until(uid)
                        text='✅ Оплата подтверждена. Подписка активирована.'
                        if end:text += f'\nПодписка действует до {end.astimezone(timezone.utc).strftime("%d.%m.%Y")}.'
                        await event.message.answer(text)
                elif status is None:
                    await event.message.answer('⏳ Оплата ещё не подтверждена. Если вы уже оплатили заказ, подождите несколько секунд и нажмите «Проверить оплату» ещё раз.')
                else:
                    await event.message.answer('Оплата не подтверждена. Заказ ещё не оплачен или был отменён.')
            except Exception:
                logger.exception('PAYMENT_VERIFY_FAILED order_id=%s user_id=%s',order_id,uid)
                await event.message.answer('Не удалось проверить оплату. Попробуйте ещё раз через несколько секунд.')
            return
        if payload.startswith('retry:'):
            try:order,link=await services.payment.retry_order(payload.split(':',1)[1],uid);await send_payment(event,order,link)
            except Exception as exc:await event.message.answer(str(exc))
            return
        if payload.startswith('cancel:'):
            await event.message.answer('Заказ отменён.' if services.payment.cancel_order(payload.split(':',1)[1],uid) else 'Заказ уже нельзя отменить.');return

    async def send_payment(event,order,link):
        if link:
            await event.message.answer(f'Заказ {order["order_id"]} создан.',attachments=_inline([[{'type':'link','text':'Перейти к оплате','url':link}],[{'type':'callback','text':'✅ Проверить оплату','payload':f'payment_check:{order["order_id"]}'}],[{'type':'callback','text':'Отмена','payload':f'cancel:{order["order_id"]}'}]]))
        else:await event.message.answer('Не удалось получить ссылку на оплату.')

    async def generate_summary_custom(event,uid,chat_id,start,end,title,user_ids=None,source_user_message_id=None):
        remaining=services.cooldown.remaining_seconds(chat_id)
        if remaining:await event.message.answer(f'До следующего запроса осталось {math.ceil(remaining/60)} минут');return
        if not services.cooldown.acquire(chat_id):
            remaining=services.cooldown.remaining_seconds(chat_id);await event.message.answer(f'До следующего запроса осталось {math.ceil(remaining/60)} минут' if remaining else 'В этом чате уже выполняется запрос.');return
        rows=services.repository.get_messages(chat_id,start,end,user_ids=user_ids)
        if not rows:services.cooldown.release(chat_id);await event.message.answer('За выбранный период сообщений нет.');return
        op=None
        if not services.subscription.is_active(uid):
            op=services.tokens.deduct_for_llm(uid,chat_id)
            if op is None:services.cooldown.release(chat_id);await event.message.answer('Токены закончились.');return
        llm_succeeded=False
        try:
            result=await services.summary.generate(chat_id=chat_id,start=start,end=end,user_ids=user_ids);llm_succeeded=True;services.cooldown.activate(chat_id);services.summary.save_run(chat_id,start,end,result);await _send(event,f'📊 Сводка за {title}\n\n{result}',services.settings_config)
        except Exception:
            if op and not llm_succeeded:services.tokens.refund(uid,chat_id,op)
            services.cooldown.release(chat_id)
            logger.exception('LLM_REQUEST_FAILED chat_id=%s user_id=%s',chat_id,uid);await event.message.answer('Не удалось сформировать сводку. Токен возвращён, если он был списан.')


    @dp.message_removed()
    async def message_removed(event):
        # По актуальной схеме MAX message_removed содержит message_id/chat_id
        # непосредственно в Update. Оставляем fallback на вложенный объект,
        # чтобы старые версии maxapi не ломали синхронизацию удаления.
        message_id=_get(event,'message_id')
        chat_id=_get(event,'chat_id')
        if message_id is None:
            nested=_get(event,'message') or _get(event,'data') or {}
            message_id=_get(nested,'message_id') or _get(nested,'mid')
            chat_id=chat_id or _get(nested,'chat_id')
        if message_id is None:
            logger.warning('MESSAGE_REMOVED_WITHOUT_ID event=%r',event)
            return
        removed=services.repository.delete_message(str(message_id))
        logger.info(
            'MESSAGE_REMOVED chat_id=%s message_id=%s deleted_from_context=%s',
            chat_id,message_id,removed,
        )

    @dp.message_edited()
    async def message_edited(event):
        message=_get(event,'message')
        if not message:
            return
        body=_get(message,'body') or {}
        mid=str(_get(body,'mid') or _get(message,'message_id') or '')
        if not mid:
            return
        chat_id=_get(_get(message,'recipient'),'chat_id')
        sender=_get(message,'sender') or {}
        uid=_get(sender,'user_id')
        first=_get(sender,'first_name','');last=_get(sender,'last_name','')
        name=' '.join(x for x in (first,last) if x).strip() or str(uid or 'Пользователь')
        text=(_get(body,'text') or '').strip()
        if chat_id is None:
            return
        if not text:
            removed=services.repository.delete_message(mid)
            logger.info('MESSAGE_EDITED_TO_NON_TEXT chat_id=%s message_id=%s deleted_from_context=%s',chat_id,mid,removed)
            return
        timestamp=_get(message,'timestamp') or _get(event,'timestamp')
        if timestamp is None:
            return
        updated=services.repository.update_message(
            MessageRecord(
                max_message_id=mid,chat_id=int(chat_id),user_id=uid,user_name=name,
                text=text,source='text',
                timestamp=datetime.fromtimestamp(int(timestamp)/1000,tz=timezone.utc),reply_to=None,
            )
        )
        logger.info('MESSAGE_EDITED chat_id=%s message_id=%s updated_in_context=%s',chat_id,mid,updated)


def summary_people_period_keyboard():
    return kb([
        [btn('За 1 день','summary_people_period:1'),btn('За 3 дня','summary_people_period:3')],
        [btn('За 7 дней','summary_people_period:7'),btn('За всё время','summary_people_period:all')],
        [btn('↩️ Назад','summary_menu')],
    ])


def extract_poll(message):
    body=_get(message,'body');attachments=_get(body,'attachments',[]) or []
    for a in attachments:
        if str(_get(a,'type','')).lower()!='poll':continue
        p=_get(a,'payload',{}) or {};pid=_get(p,'poll_id') or _get(p,'id') or _get(a,'poll_id')
        q=_get(p,'question') or _get(p,'text') or 'Опрос';opts=_get(p,'options') or _get(p,'answers') or []
        normalized=[];total=0
        for i,o in enumerate(opts):
            txt=_get(o,'text') or _get(o,'title') or str(i+1);votes=_get(o,'votes') or _get(o,'vote_count') or 0
            try:votes=int(votes)
            except Exception:votes=0
            normalized.append({'text':txt,'votes':votes});total+=votes
        if pid is None:return None
        return {'poll_id':str(pid),'question':q,'data':{'options':normalized},'total_votes':total}
    return None

def render_poll(p):
    return 'Опрос: '+p['question']+'\n'+'\n'.join(f"{i+1}. {x['text']} — {x['votes']} голосов" for i,x in enumerate(p['data']['options']))+f"\nВсего голосов: {p['total_votes']}"
