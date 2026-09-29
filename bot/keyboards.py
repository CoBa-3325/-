"""MAX inline keyboards. Backend authorization is performed separately."""
from maxapi import types

def kb(rows): return [types.ButtonsPayload(buttons=rows).pack()]
def btn(text,payload): return {'type':'callback','text':text,'payload':payload}

def main_menu(active=False, role='user', chat_type='chat'):
    """Главное меню. Сводка доступна только в групповых беседах."""
    rows=[]
    if chat_type=='chat': rows.append([btn('📊 Сводка','summary_menu')])
    rows += [[btn('👤 Личный кабинет','cabinet')],[btn('🎟 Ввести промокод','redeem_promo')]]
    if role=='user': rows += [[btn('💳 Купить токены','buy_tokens'),btn('⭐ Оформить подписку','buy_subscription')]]
    if role in ('admin','creator'): rows.append([btn('🛠 Админ-панель','admin_panel')])
    return kb(rows)

def private_start_menu(active=False, role='user'):
    """Меню первого сообщения в ЛС. Сводка доступна только в групповых чатах."""
    return main_menu(active, role, chat_type='dialog')

def back_keyboard(target='back'):
    """Клавиатура с единственной кнопкой возврата (например, для отмены ввода)."""
    return kb([[btn('↩️ Назад',target)]])

def cabinet_button_keyboard():
    """Кнопка перехода в личный кабинет (например, после успешной оплаты)."""
    return kb([[btn('👤 Личный кабинет','cabinet')]])

def cabinet_keyboard(active=False, role='user'):
    rows=[[btn('⭐ Оформить подписку','buy_subscription'),btn('💳 Купить токены','buy_tokens')] if role=='user' else [btn('🛠 Админ-панель','admin_panel')], [btn('🎟 Ввести промокод','redeem_promo')],[btn('↩️ Назад','back')]]
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
        [btn('За 7 дней','summary_period:7'),btn('За всё время','summary_period:all')],
        [btn('↩️ Назад','summary_menu')],
    ])

def admin_panel_keyboard(role):
    rows=[[btn('🎟 Выдать промокод','promo_menu')]]
    if role=='creator':rows += [[btn('👥 Создатели','creators')],[btn('🗑 Снять администратора','admins')],[btn('🚪 Выйти из роли создателя','creator_exit')]]
    rows += [[btn('↩️ Назад','back')]]
    return kb(rows)

def promo_reward_keyboard(role):
    rows=[[btn('⭐ Подписки','promo_reward:subscription')],[btn('🎟 Токены','promo_reward:tokens')],[btn('👑 Администраторы','promo_reward:admin')]]
    if role=='creator':rows.append([btn('👑 Создатели','promo_reward:creator')])
    rows.append([btn('↩️ Назад','admin_panel')]);return kb(rows)

def promo_subscription_keyboard():return kb([[btn('Подписка на 1 месяц','promo_sub:1'),btn('Подписка на 3 месяца','promo_sub:3')],[btn('Подписка на 6 месяцев','promo_sub:6'),btn('Вечная подписка','promo_sub:unlimited')],[btn('↩️ Назад','promo_menu')]])
def promo_usage_keyboard():return kb([[btn('Одноразовый','promo_usage:once')],[btn('Многоразовый','promo_usage:limited')],[btn('Без ограничений','promo_usage:unlimited')],[btn('↩️ Назад','promo_menu')]])
def token_buy_keyboard(price=300):return kb([[btn(f'Купить 10 токенов — {price} ₽','token_buy:10')],[btn('↩️ Назад','back')]])
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
