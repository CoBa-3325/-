"""MAX inline keyboards. Backend authorization is performed separately."""
from maxapi import types

def kb(rows): return [types.ButtonsPayload(buttons=rows).pack()]
def btn(text,payload): return {'type':'callback','text':text,'payload':payload}

def main_menu(active=False, role='user', chat_type='chat'):
    """Главное меню.

    В беседах доступен весь функционал, в ЛС — только подписка, а для
    администраторов дополнительно вход в админ-панель.
    """
    rows=[]
    if chat_type=='chat':
        rows.append([btn('📊 Отчет','summary_menu')])
        rows.append([btn('👤 Личный кабинет','cabinet')])
        if role=='user': rows.append([btn('⭐ Оформить подписку','buy_subscription')])
    else:
        rows.append([btn('⭐ Оформить подписку','buy_subscription')])
    if role=='admin': rows.append([btn('🛠 Админ-панель','admin_panel')])
    return kb(rows)

def private_start_menu(active=False, role='user'):
    """Меню первого сообщения в ЛС."""
    return main_menu(active, role, chat_type='dialog')

def group_added_keyboard():
    """Минимальное меню приветствия при добавлении бота в беседу."""
    return kb([
        [btn('📊 Отчет','summary_menu')],
    ])

def group_start_keyboard():
    """В беседе команда /начать показывает только вход в отчёты."""
    return kb([[btn('📊 Отчет','summary_menu')]])

def back_keyboard(target='back'):
    """Клавиатура с единственной кнопкой возврата (например, для отмены ввода)."""
    return kb([[btn('↩️ Назад',target)]])

def cabinet_button_keyboard():
    """Кнопка перехода в личный кабинет (например, после успешной оплаты)."""
    return kb([[btn('👤 Личный кабинет','cabinet')]])

def support_reply_keyboard(ticket_id):
    """Кнопки управления обращением для сотрудника поддержки."""
    return kb([
        [btn('✉️ Ответить',f'ticket_reply:{ticket_id}')],
        [btn('✅ Закрыть тикет',f'ticket_close:{ticket_id}')],
    ])

def ticket_user_keyboard(ticket_id):
    """Кнопка закрытия обращения пользователем."""
    return kb([[btn('✅ Закрыть обращение',f'ticket_user_close:{ticket_id}')]])

def cabinet_keyboard(active=False, role='user'):
    if role=='admin':
        rows=[[btn('🛠 Админ-панель','admin_panel')],[btn('🎟 Ввести промокод','redeem_promo')],[btn('↩️ Назад','back')]]
    else:
        rows=[[btn('⭐ Оформить подписку','buy_subscription')],[btn('🎟 Ввести промокод','redeem_promo')],[btn('↩️ Назад','back')]]
    return kb(rows)

def summary_type_keyboard():
    return kb([
        [btn('📊 Общее','summary_type:general')],
        [btn('👥 По конкретным людям','summary_type:people')],
        [btn('↩️ Назад','back')],
    ])

def summary_period_keyboard():
    return kb([
        [btn('За 1 день','summary_period:1'),btn('За 3 дня','summary_period:3')],
        [btn('За 7 дней','summary_period:7'),btn('За 30 дней','summary_period:30')],
        [btn('↩️ Назад','summary_menu')],
    ])

def admin_panel_keyboard(role=None):
    return kb([
        [btn('📋 Беседы','admin_chats')],
        [btn('↩️ Назад','back')],
    ])

def subscription_keyboard(price1=99,price3=259,price6=559):return kb([[btn(f'1 месяц — {price1} ₽','sub_tariff:1_month'),btn(f'3 месяца — {price3} ₽','sub_tariff:3_months')],[btn(f'6 месяцев — {price6} ₽','sub_tariff:6_months')],[btn('↩️ Назад','back')]])
def user_list_keyboard(rows,prefix,back='admin_panel'):
    out=[[btn((' '.join(x for x in (r['first_name'],r['last_name']) if x).strip() or str(r['user_id']))[:40],f'{prefix}:{r["user_id"]}')] for r in rows]
    out.append([btn('↩️ Назад',back)]);return kb(out)
def confirm_keyboard(yes,no='admin_panel'):return kb([[btn('Да, подтвердить',yes)],[btn('← Назад',no)]])


def summary_people_keyboard(authors, selected=None):
    selected = {int(x) for x in (selected or [])}
    rows = []
    for author in authors:
        user_id = int(author['user_id'])
        name = author['user_name'] or str(user_id)
        prefix = '☑️' if user_id in selected else '⬜'
        rows.append([btn(f'{prefix} {name[:34]}', f'summary_people_toggle:{user_id}')])
    if selected:
        rows.append([btn('✅ Готово', 'summary_people_done')])
    rows.append([btn('↩️ Назад', 'summary_menu')])
    return kb(rows)
