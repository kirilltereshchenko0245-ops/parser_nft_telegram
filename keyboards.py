from aiogram.utils.keyboard import ReplyKeyboardBuilder,InlineKeyboardBuilder
from aiogram.types import KeyboardButton,InlineKeyboardButton
from urllib.parse import quote

def main_menu(admin=False,premium=False):
    b=ReplyKeyboardBuilder();
    b.row(KeyboardButton(text='🔎 Новый поиск'),KeyboardButton(text='🆔 Поиск по ID'))
    b.row(KeyboardButton(text='🎲 Случайный'),KeyboardButton(text='📚 История'))
    b.row(KeyboardButton(text='⭐ Избранное'),KeyboardButton(text='🎁 Модели'))
    b.row(KeyboardButton(text='📊 Профиль'),KeyboardButton(text='❓ Помощь'))
    if not premium:b.row(KeyboardButton(text='💎 Premium'))
    if admin:b.row(KeyboardButton(text='⚙️ Админка'))
    return b.as_markup(resize_keyboard=True,input_field_placeholder='Выберите действие…')
def cancel():
    b=ReplyKeyboardBuilder();b.add(KeyboardButton(text='✖️ Отмена'));return b.as_markup(resize_keyboard=True)
def stop():
    b=ReplyKeyboardBuilder();b.add(KeyboardButton(text='⏹ Остановить'));return b.as_markup(resize_keyboard=True)
def gift_card(g,favorite=False):
    b=InlineKeyboardBuilder();n=g.get('giftNumber');name=(g.get('giftName') or g.get('model') or '').replace(' ','')
    row=[]
    if g.get('username'):row.append(InlineKeyboardButton(text='👤 Владелец',url=f'https://t.me/{g["username"]}'))
    if name and n is not None:row.append(InlineKeyboardButton(text='🎁 Открыть',url=f'https://t.me/nft/{quote(name)}-{n}'))
    if row:b.row(*row)
    if n is not None:b.row(InlineKeyboardButton(text='★ Убрать из избранного' if favorite else '☆ В избранное',callback_data=f'fav:{n}:{quote(g.get("model") or "")}'))
    return b.as_markup()
def history_item(sid,status):
    b=InlineKeyboardBuilder();b.row(InlineKeyboardButton(text='📋 Результаты',callback_data=f'hist:{sid}'))
    if status in ('stopped','error'):b.row(InlineKeyboardButton(text='▶️ Продолжить',callback_data=f'resume:{sid}'))
    return b.as_markup()
def models_nav(page,total,per_page=8):
    b=InlineKeyboardBuilder();pages=max(1,(total+per_page-1)//per_page);row=[]
    if page>1:row.append(InlineKeyboardButton(text='‹',callback_data=f'models:{page-1}'))
    row.append(InlineKeyboardButton(text=f'{page}/{pages}',callback_data='noop'))
    if page<pages:row.append(InlineKeyboardButton(text='›',callback_data=f'models:{page+1}'))
    b.row(*row);return b.as_markup()
def admin_menu():
    b=InlineKeyboardBuilder();b.row(InlineKeyboardButton(text='📈 Статистика',callback_data='adm:stats'));b.row(InlineKeyboardButton(text='👥 Пользователи',callback_data='adm:users'));return b.as_markup()
