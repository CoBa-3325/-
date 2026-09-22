"""MAX event handlers implementing the complete user/admin flow."""
from __future__ import annotations
import logging, math, re
from datetime import datetime, timezone
from maxapi import types
from  bot.commands import HELP_TEXT
from  bot.keyboards import *
from  services.period_service import period_for_interval
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
    for i,c in enumerate(chunks): await event.message.answer(c,attachments=attachments if i==0 else None)

def _role(services,uid): return services.roles.get_role(uid)

def register_handlers(dp,services):
    @dp.bot_started()
    async def bot_started(event):
        uid=getattr(event,'user_id',None);chat_id=event.chat_id;chat_type='unknown';title=None
        try:
            chat=await event.fetch_chat();chat_type=str(getattr(chat,'type','unknown')).lower();title=getattr(chat,'title',None)
        except Exception:pass
        if uid:services.repository.upsert_user(uid)
        services.repository.upsert_chat(chat_id,chat_type,title)
        if uid: await event.bot.send_message(chat_id=chat_id,text='Привет! Я собираю контекст групповых обсуждений и по запросу формирую сводку. Откройте личный кабинет для управления доступом.')

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
            await _send(event,HELP_TEXT,services.settings_config,main_menu(services.subscription.is_active(uid),_role(services,uid)));return
        if command in ('/cabinet','/buy'):
            await _send(event,services.cabinet.render(uid),services.settings_config,cabinet_keyboard(services.subscription.is_active(uid),_role(services,uid)));return
        if command=='/buy_tokens':
            await event.message.answer('Осталось токенов: %s'%services.tokens.balance(uid),attachments=token_buy_keyboard());return
        if command=='/buy_subscription':
            await show_subscription(event,uid);return
        if command=='/summary':
            if ctype!='chat':
                await event.message.answer('Команда /summary доступна только в беседах.');return
            days=7
            if args and re.fullmatch(r'\d+',args[0]):days=max(1,min(int(args[0]),3650))
            await generate_summary(event,uid,chat_id,days);return
        if command=='/settings':
            # Explicitly removed by the specification.
            return
        if text.startswith('/'):return

        if ctype!='chat':return
        # Free trial is user-wide, never per chat.
        services.tokens.ensure_trial(uid)
        audio=services.voice.find_audio_attachment(message)
        if audio is not None:
            try:
                voice_text=await services.voice.process_voice_message(message)
                services.message.save_voice(max_message_id=mid,chat_id=chat_id,user_id=uid,user_name=name,text=voice_text,timestamp_ms=int(timestamp),reply_to=None)
                logger.info('VOICE_MESSAGE_SAVED chat_id=%s user_id=%s',chat_id,uid)
            except Exception:
                await event.message.answer('Не удалось расшифровать голосовое сообщение. Попробуйте ещё раз.')
            return

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

    async def generate_summary(event,uid,chat_id,days):
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

        if payload=='cabinet':await event.message.answer(services.cabinet.render(uid),attachments=cabinet_keyboard(services.subscription.is_active(uid),role));return
        if payload=='back':
            await event.message.answer('Главное меню:',attachments=main_menu(services.subscription.is_active(uid),role));return
        if payload=='summary_menu':
            if _chat_type(message)!='chat':await event.message.answer('Команда /summary доступна только в беседах.');return
            await event.message.answer('За какой срок сформировать сводку?',attachments=summary_period_keyboard());return
        if payload=='redeem_promo':
            services.repository.set_user_state(uid,'redeem_promo',{});await event.message.answer('Введите промокод');return
        if payload=='buy_tokens':await event.message.answer('Осталось токенов: %s'%services.tokens.balance(uid),attachments=token_buy_keyboard());return
        if payload=='buy_subscription':await show_subscription(event,uid);return
        if payload=='token_buy:10':
            order,link=await services.payment.create_order(uid,'tokens','10',None);await send_payment(event,order,link);return
        if payload.startswith('sub_tariff:'):
            tariff=payload.split(':',1)[1]
            if role!='user':await event.message.answer('Для административных ролей подписка бесконечная.');return
            order,link=await services.payment.create_order(uid,'subscription',tariff,None);await send_payment(event,order,link);return
        if payload.startswith('summary_period:'):
            val=payload.split(':',1)[1]
            if val=='all':
                from datetime import datetime
                period_start=datetime(1970,1,1,tzinfo=timezone.utc);days=999999
                # Generate directly without converting all-time to an enormous display period.
                await generate_summary_custom(event,uid,_get(_get(message,'recipient'),'chat_id'),period_start,datetime.now(timezone.utc),'всё время');return
            await generate_summary(event,uid,_get(_get(message,'recipient'),'chat_id'),max(1,min(int(val),3650)));return
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
        if payload.startswith('retry:'):
            try:order,link=await services.payment.retry_order(payload.split(':',1)[1],uid);await send_payment(event,order,link)
            except Exception as exc:await event.message.answer(str(exc))
            return
        if payload.startswith('cancel:'):
            await event.message.answer('Заказ отменён.' if services.payment.cancel_order(payload.split(':',1)[1],uid) else 'Заказ уже нельзя отменить.');return

    async def send_payment(event,order,link):
        if link:
            await event.message.answer(f'Заказ {order["order_id"]} создан.',attachments=_inline([[{'type':'link','text':'Перейти к оплате','url':link}],[{'type':'callback','text':'Отмена','payload':f'cancel:{order["order_id"]}'}]]))
        else:await event.message.answer('Не удалось получить ссылку на оплату.')

    async def generate_summary_custom(event,uid,chat_id,start,end,title):
        remaining=services.cooldown.remaining_seconds(chat_id)
        if remaining:await event.message.answer(f'До следующего запроса осталось {math.ceil(remaining/60)} минут');return
        if not services.cooldown.acquire(chat_id):
            remaining=services.cooldown.remaining_seconds(chat_id);await event.message.answer(f'До следующего запроса осталось {math.ceil(remaining/60)} минут' if remaining else 'В этом чате уже выполняется запрос.');return
        rows=services.repository.get_messages(chat_id,start,end)
        if not rows:services.cooldown.release(chat_id);await event.message.answer('За выбранный период сообщений нет.');return
        op=None
        if not services.subscription.is_active(uid):
            op=services.tokens.deduct_for_llm(uid,chat_id)
            if op is None:services.cooldown.release(chat_id);await event.message.answer('Токены закончились.');return
        llm_succeeded=False
        try:
            result=await services.summary.generate(chat_id=chat_id,start=start,end=end);llm_succeeded=True;services.cooldown.activate(chat_id);services.summary.save_run(chat_id,start,end,result);await _send(event,f'📊 Сводка за {title}\n\n{result}',services.settings_config)
        except Exception:
            if op and not llm_succeeded:services.tokens.refund(uid,chat_id,op)
            services.cooldown.release(chat_id)
            logger.exception('LLM_REQUEST_FAILED chat_id=%s user_id=%s',chat_id,uid);await event.message.answer('Не удалось сформировать сводку. Токен возвращён, если он был списан.')


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
