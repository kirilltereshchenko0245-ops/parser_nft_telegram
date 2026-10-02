import asyncio, html, logging, os, random, time
from aiogram import Bot,Dispatcher,F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State,StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message,CallbackQuery,BufferedInputFile
from dotenv import load_dotenv
from database import Database
from peek_api import PeekAPI,AVAILABLE_MODELS,normalize_model_name
from keyboards import main_menu,cancel,stop,gift_card,history_item,models_nav,admin_menu

load_dotenv(); logging.basicConfig(level=logging.INFO,format='%(asctime)s | %(levelname)s | %(message)s'); log=logging.getLogger('giftparser')
TOKEN=os.getenv('BOT_TOKEN'); OWNER_ID=int(os.getenv('OWNER_ID','0')); CONCURRENCY=int(os.getenv('PEEK_CONCURRENCY','5'))
if not TOKEN: raise RuntimeError('BOT_TOKEN не указан в .env')
bot=Bot(TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML)); dp=Dispatcher(storage=MemoryStorage()); db=Database(); api=PeekAPI(CONCURRENCY)

class S(StatesGroup): model=State(); range=State(); id_model=State(); id_number=State(); random_count=State()
def is_admin(uid): return uid==OWNER_ID
def user_menu(uid):
    u=db.get_user(uid) or {};return main_menu(is_admin(uid),bool(u.get('is_premium')))

def gift_card_text(g):
    name=html.escape(str(g.get('giftName') or g.get('model') or 'Telegram Gift'))
    lines=[f'🎁 <b>{name}</b>',f'🆔 <code>#{g.get("giftNumber")}</code>']
    for icon,label,key in [('✨','Модель','model'),('🌸','Узор','pattern'),('🌈','Фон','backdrop')]:
        if g.get(key):lines.append(f'{icon} {label}: <code>{html.escape(str(g[key]))}</code>')
    if g.get('username'):lines.append(f'👤 Владелец: <b>@{html.escape(str(g["username"]))}</b>')
    elif g.get('userId'):lines.append(f'👤 Telegram ID: <code>{g["userId"]}</code>')
    return '\n'.join(lines)

def parse_range(text):
    t=text.strip().replace(' ','').replace('—','-').replace('–','-')
    parts=t.split('-')
    if len(parts)!=2:raise ValueError
    a,b=map(int,parts)
    if a<1 or b<a or b-a+1>10000:raise ValueError
    return a,b

def progress_bar(done,total,width=12):
    filled=int(width*done/total) if total else 0;return '█'*filled+'░'*(width-filled)

def pretty_time(sec):
    sec=max(0,int(sec));
    if sec<60:return f'{sec}с'
    return f'{sec//60}м {sec%60}с'

@dp.message.middleware()
async def access(handler,event:Message,data):
    if not event.from_user:return await handler(event,data)
    uid=event.from_user.id;db.upsert_user(uid,event.from_user.username,event.from_user.first_name)
    if db.is_banned(uid):await event.answer('🚫 <b>Доступ ограничен.</b>');return
    return await handler(event,data)

@dp.message(Command('start'))
async def start(m:Message,state:FSMContext):
    await state.clear();u=db.get_user(m.from_user.id) or {}; trial=u.get('trial_searches_left',25)
    status='💎 Premium' if u.get('is_premium') else f'🆓 Trial · {trial} ID'
    await m.answer(f'🎁 <b>GiftParser</b> <code>V5.0</code>\n\nБыстрый поиск Telegram NFT-подарков через Peek.tg.\n\n{status}\n\nВыберите действие 👇',reply_markup=user_menu(m.from_user.id))

@dp.message(Command('stop'))
@dp.message(F.text=='⏹ Остановить')
async def stop_search(m:Message): db.stop(m.from_user.id);await m.answer('⏹ <b>Остановка запрошена.</b> Текущие запросы завершатся и прогресс сохранится.')

@dp.message(F.text=='✖️ Отмена')
async def cancel_action(m:Message,state:FSMContext):await state.clear();await m.answer('↩️ Действие отменено.',reply_markup=user_menu(m.from_user.id))

async def allowed(uid,count):
    u=db.get_user(uid) or {}
    return is_admin(uid) or bool(u.get('is_premium')) or count<=int(u.get('trial_searches_left',0))

@dp.message(F.text=='🔎 Новый поиск')
async def new_search(m:Message,state:FSMContext):
    if not await allowed(m.from_user.id,1):return await m.answer('🆓 Лимит Trial закончился.\n\n💎 Подключите Premium для продолжения.')
    await state.set_state(S.model);await m.answer('🔎 <b>Новый поиск</b>\n\nВведите модель.\nНапример: <code>Eternal Rose</code>',reply_markup=cancel())

@dp.message(S.model)
async def model_step(m:Message,state:FSMContext):
    name=normalize_model_name(m.text or '')
    if len(name)<2:return await m.answer('❌ Введите название модели.')
    await state.update_data(model=name);await state.set_state(S.range);await m.answer(f'🎁 Модель: <b>{html.escape(name)}</b>\n\nВведите диапазон, например <code>13000-13500</code>.\nМаксимум: 10 000 ID.',reply_markup=cancel())

@dp.message(S.range)
async def range_step(m:Message,state:FSMContext):
    try:a,b=parse_range(m.text or '')
    except ValueError:return await m.answer('❌ Формат: <code>начало-конец</code>. Максимум 10 000 ID.')
    count=b-a+1
    if not await allowed(m.from_user.id,count):
        u=db.get_user(m.from_user.id) or {};return await m.answer(f'🆓 Осталось <b>{u.get("trial_searches_left",0)}</b> проверок, а диапазон содержит <b>{count}</b> ID.')
    data=await state.get_data();await state.clear();await run_search(m,data['model'],a,b)

async def run_search(m,model,start,end,existing_sid=None):
    uid=m.from_user.id;db.clear_stop(uid);total=end-start+1
    if existing_sid is None:sid=db.create_search(uid,model,start,end);checked=0;found=0;next_id=start
    else:
        x=db.get_search(existing_sid,uid);sid=existing_sid;checked=x['checked_count'];found=x['found_count'];next_id=x['last_checked_id']+1
        if next_id>end:return await m.answer('✅ Этот поиск уже полностью обработан.')
    t0=time.monotonic(); msg=await m.answer(f'🔎 <b>Поиск запущен</b>\n\n🎁 {html.escape(model)}\n🆔 <code>{next_id} → {end}</code>\n\n{progress_bar(checked,total)}  <b>{int(checked/total*100)}%</b>\n📦 Проверено: <b>{checked}</b>\n✨ Найдено: <b>{found}</b>',reply_markup=stop())
    pending=[]; batch_size=max(5,CONCURRENCY*3); current=next_id
    try:
        while current<=end:
            if db.should_stop(uid):
                db.update_search(sid,current-1,checked,found,'stopped');await msg.edit_text(f'⏹ <b>Поиск остановлен</b>\n\n🎁 {html.escape(model)}\n📦 Проверено: <b>{checked}</b> / {total}\n✨ Найдено: <b>{found}</b>\n\n▶️ Продолжить можно из раздела «📚 История».',reply_markup=user_menu(uid));return
            nums=list(range(current,min(end+1,current+batch_size))); results=await asyncio.gather(*(api.get_gift(model,n) for n in nums),return_exceptions=True)
            for n,g in zip(nums,results):
                checked+=1
                if isinstance(g,Exception):log.warning('Peek %s #%s: %s',model,n,g);continue
                if g:
                    found+=1;db.add_gift(sid,uid,g);await m.answer(gift_card_text(g),reply_markup=gift_card(g,db.is_favorite(uid,g.get('giftNumber'),g.get('model'))))
            last=nums[-1];db.update_search(sid,last,checked,found);current=last+1
            elapsed=time.monotonic()-t0;rate=checked/elapsed if elapsed else 0;eta=(total-checked)/rate if rate else 0
            try:await msg.edit_text(f'🔎 <b>Поиск идёт</b>\n\n🎁 {html.escape(model)}\n{progress_bar(checked,total)}  <b>{int(checked/total*100)}%</b>\n\n📦 Проверено: <b>{checked}</b> / {total}\n✨ Найдено: <b>{found}</b>\n⚡ Скорость: <b>{rate:.1f} ID/с</b>\n⏱ Осталось: <b>~{pretty_time(eta)}</b>',reply_markup=stop())
            except Exception:pass
        db.update_search(sid,end,checked,found,'completed')
        u=db.get_user(uid) or {}
        if not is_admin(uid) and not u.get('is_premium'):db.consume_trial(uid,checked)
        elapsed=time.monotonic()-t0
        await msg.edit_text(f'✅ <b>Поиск завершён</b>\n\n🎁 {html.escape(model)}\n📦 Проверено: <b>{checked}</b>\n✨ Найдено: <b>{found}</b>\n⚡ Скорость: <b>{checked/elapsed:.1f} ID/с</b>\n⏱ Время: <b>{pretty_time(elapsed)}</b>\n\n📚 Результаты сохранены в истории.',reply_markup=user_menu(uid))
    except Exception:
        log.exception('Search failed uid=%s sid=%s',uid,sid);db.update_search(sid,max(start-1,current-1),checked,found,'error')
        await msg.edit_text(f'⚠️ <b>Поиск прерван из-за ошибки</b>\n\n📦 Прогресс сохранён: {checked}/{total}\n✨ Найдено: {found}\n\nОткройте «📚 История» и продолжите позже.',reply_markup=user_menu(uid))

@dp.message(F.text=='🆔 Поиск по ID')
async def id_search(m:Message,state:FSMContext):await state.set_state(S.id_model);await m.answer('🆔 <b>Поиск по ID</b>\n\nВведите модель:',reply_markup=cancel())
@dp.message(S.id_model)
async def id_model(m:Message,state:FSMContext):
    name=normalize_model_name(m.text or '');await state.update_data(model=name);await state.set_state(S.id_number);await m.answer(f'🎁 <b>{html.escape(name)}</b>\n\nВведите ID:',reply_markup=cancel())
@dp.message(S.id_number)
async def id_number(m:Message,state:FSMContext):
    try:n=int((m.text or '').strip());assert n>0
    except:return await m.answer('❌ ID должен быть положительным числом.')
    data=await state.get_data();await state.clear();g=await api.get_gift(data['model'],n)
    if not g:return await m.answer(f'🔎 <code>#{n}</code> не найден.',reply_markup=user_menu(m.from_user.id))
    await m.answer(gift_card_text(g),reply_markup=gift_card(g,db.is_favorite(m.from_user.id,n,g.get('model'))));await m.answer('Готово 👌',reply_markup=user_menu(m.from_user.id))

@dp.message(F.text=='🎲 Случайный')
async def random_search(m:Message,state:FSMContext):
    if not await allowed(m.from_user.id,1):return await m.answer('🆓 Лимит Trial закончился.')
    await state.set_state(S.random_count);await m.answer('🎲 <b>Случайный поиск</b>\n\nСколько подарков проверить? <code>1-20</code>',reply_markup=cancel())
@dp.message(S.random_count)
async def random_count(m:Message,state:FSMContext):
    try:c=int(m.text);assert 1<=c<=20
    except:return await m.answer('❌ Введите число от 1 до 20.')
    if not await allowed(m.from_user.id,c):return await m.answer('🆓 Недостаточно Trial-проверок.')
    await state.clear();model=random.choice(AVAILABLE_MODELS);nums=random.sample(range(1,100000),c);await m.answer(f'🎲 Модель: <b>{model}</b>\n🔢 Проверяю {c} случайных ID…')
    found=0
    for n in nums:
        g=await api.get_gift(model,n)
        if g:found+=1;await m.answer(gift_card_text(g),reply_markup=gift_card(g))
    u=db.get_user(m.from_user.id) or {}
    if not is_admin(m.from_user.id) and not u.get('is_premium'):db.consume_trial(m.from_user.id,c)
    await m.answer(f'🎲 Готово. Найдено: <b>{found}</b> из {c}.',reply_markup=user_menu(m.from_user.id))

@dp.callback_query(F.data.startswith('fav:'))
async def favorite(c:CallbackQuery):
    parts=c.data.split(':',2);n=int(parts[1]);model=parts[2]
    from urllib.parse import unquote
    model=unquote(model)
    if db.is_favorite(c.from_user.id,n,model):db.remove_favorite(c.from_user.id,n,model);await c.answer('☆ Убрано из избранного')
    else:db.add_favorite(c.from_user.id,n,model,None);await c.answer('⭐ Добавлено в избранное')

@dp.message(F.text=='⭐ Избранное')
async def favorites(m:Message):
    rows=db.favorites(m.from_user.id)
    if not rows:return await m.answer('⭐ <b>Избранное пусто</b>\n\nНажимай «☆ В избранное» под найденными подарками.')
    await m.answer(f'⭐ <b>Избранное</b> · {len(rows)}')
    for r in rows:
        g={'giftNumber':r['gift_number'],'model':r['model'],'giftName':r['model'],'username':r['username']}
        await m.answer(gift_card_text(g),reply_markup=gift_card(g,True))

@dp.message(F.text=='📚 История')
async def history(m:Message):
    rows=db.history(m.from_user.id)
    if not rows:return await m.answer('📚 <b>История пуста</b>\n\nЗапусти первый поиск.')
    for x in rows:
        total=x['end_id']-x['start_id']+1;p=int(x['checked_count']/total*100) if total else 0
        status={'completed':'✅ завершён','stopped':'⏹ остановлен','error':'⚠️ ошибка','in_progress':'🔎 выполняется'}.get(x['status'],x['status'])
        await m.answer(f'📌 <b>Поиск #{x["id"]}</b>\n🎁 {html.escape(x["model"])}\n🆔 {x["start_id"]}–{x["end_id"]}\n📊 {p}% · {x["checked_count"]}/{total}\n✨ Найдено: <b>{x["found_count"]}</b>\n{status}',reply_markup=history_item(x['id'],x['status']))

@dp.callback_query(F.data.startswith('hist:'))
async def history_open(c:CallbackQuery):
    sid=int(c.data.split(':')[1]);x=db.get_search(sid,c.from_user.id)
    if not x:return await c.answer('Поиск не найден',show_alert=True)
    gs=db.get_gifts(sid);await c.answer(f'Найдено: {len(gs)}')
    for r in gs:
        g={'giftNumber':r['gift_number'],'model':r['model'],'giftName':r['model'],'pattern':r['pattern'],'backdrop':r['backdrop'],'username':r['username'],'userId':r['user_id_owner']}
        await c.message.answer(gift_card_text(g),reply_markup=gift_card(g,db.is_favorite(c.from_user.id,r['gift_number'],r['model'])))
    if not gs:await c.message.answer('Пока нет сохранённых находок.')

@dp.callback_query(F.data.startswith('resume:'))
async def resume(c:CallbackQuery):
    sid=int(c.data.split(':')[1]);x=db.get_search(sid,c.from_user.id)
    if not x:return await c.answer('Поиск не найден',show_alert=True)
    if x['status'] not in ('stopped','error'):return await c.answer('Этот поиск уже завершён.')
    await c.answer('▶️ Продолжаю…');await run_search(c.message,x['model'],x['start_id'],x['end_id'],sid)

@dp.message(F.text=='🎁 Модели')
async def models(m:Message):await show_models(m,1)
async def show_models(target,page):
    per=8;start=(page-1)*per;items=AVAILABLE_MODELS[start:start+per]
    text='🎁 <b>Каталог моделей</b>\n\n'+'\n'.join(f'• <code>{x}</code>' for x in items)
    await target.answer(text,reply_markup=models_nav(page,len(AVAILABLE_MODELS),per))
@dp.callback_query(F.data.startswith('models:'))
async def models_page(c:CallbackQuery):await c.answer();await show_models(c.message,int(c.data.split(':')[1]))
@dp.callback_query(F.data=='noop')
async def noop(c:CallbackQuery):await c.answer()

@dp.message(F.text=='📊 Профиль')
async def profile(m:Message):
    u=db.get_user(m.from_user.id) or {};s=db.stats();status='💎 Premium' if u.get('is_premium') else '🆓 Trial'
    await m.answer(f'👤 <b>Профиль</b>\n\n🆔 <code>{m.from_user.id}</code>\n👤 Username: {("@"+m.from_user.username) if m.from_user.username else "—"}\n💎 Статус: <b>{status}</b>\n🎫 Осталось Trial: <b>{u.get("trial_searches_left",25)}</b>\n\n🔎 Поисков: <b>{u.get("total_searches",0)}</b>\n🎁 Находок: <b>{u.get("total_found",0)}</b>\n\n🌐 Пользователей бота: <b>{s["users"]}</b>')

@dp.message(F.text=='💎 Premium')
async def premium(m:Message):await m.answer('💎 <b>Premium</b>\n\nБезлимитный поиск и дополнительные функции.\n\nПлатёжную интеграцию лучше подключить отдельно через безопасные переменные окружения — секретные ключи не должны лежать в коде.')
@dp.message(F.text=='❓ Помощь')
async def help_cmd(m:Message):await m.answer('❓ <b>Как пользоваться</b>\n\n🔎 <b>Новый поиск</b> — модель + диапазон ID.\n🆔 <b>Поиск по ID</b> — проверка одного подарка.\n🎲 <b>Случайный</b> — быстрый тест случайных ID.\n📚 <b>История</b> — результаты и продолжение остановленного поиска.\n⭐ <b>Избранное</b> — сохранённые находки.\n\nСовет: для больших диапазонов лучше запускать поиск частями и продолжать из истории.')

@dp.message(F.text=='⚙️ Админка')
async def admin(m:Message):
    if not is_admin(m.from_user.id):return
    await m.answer('⚙️ <b>Админ-панель</b>',reply_markup=admin_menu())
@dp.callback_query(F.data=='adm:stats')
async def adm_stats(c:CallbackQuery):
    if not is_admin(c.from_user.id):return await c.answer('Нет доступа',show_alert=True)
    s=db.stats();await c.answer();await c.message.answer(f'📈 <b>Статистика</b>\n\n👥 Пользователей: <b>{s["users"]}</b>\n💎 Premium: <b>{s["premium"]}</b>\n🚫 Банов: <b>{s["banned"]}</b>\n🔎 Поисков: <b>{s["searches"]}</b>\n🎁 Находок: <b>{s["found"]}</b>')
@dp.callback_query(F.data=='adm:users')
async def adm_users(c:CallbackQuery):
    if not is_admin(c.from_user.id):return await c.answer('Нет доступа',show_alert=True)
    await c.answer();await c.message.answer('👥 Для управления конкретным пользователем используйте его Telegram ID в отдельной админ-команде. Это намеренно не сделано через случайные кнопки, чтобы не менять аккаунты по ошибке.')

@dp.message(Command('ban'))
async def ban(m:Message):
    if not is_admin(m.from_user.id):return
    try:uid=int(m.text.split(maxsplit=1)[1])
    except:return await m.answer('Использование: <code>/ban 123456789</code>')
    db.set_ban(uid,True);await m.answer(f'🚫 Пользователь <code>{uid}</code> заблокирован.')
@dp.message(Command('unban'))
async def unban(m:Message):
    if not is_admin(m.from_user.id):return
    try:uid=int(m.text.split(maxsplit=1)[1])
    except:return await m.answer('Использование: <code>/unban 123456789</code>')
    db.set_ban(uid,False);await m.answer(f'✅ Пользователь <code>{uid}</code> разблокирован.')

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    try:await dp.start_polling(bot)
    finally:await api.close();await bot.session.close()
if __name__=='__main__':asyncio.run(main())
