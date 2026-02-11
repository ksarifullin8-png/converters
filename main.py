import asyncio
import sqlite3
import os
import subprocess
from datetime import datetime, timedelta
from collections import deque
import shutil
import sys

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, LabeledPrice, InlineQuery,
    InlineQueryResultArticle, InputTextMessageContent,
    ChosenInlineResult
)
from aiogram.enums import ContentType
from aiogram.filters import Command

# ================== CONFIG ==================

BOT_TOKEN = "8412540063:AAGS27AFzCUtS9JiOsjBADXJ4-sJkmoceoQ"
ADMIN_ID = 7546928092

DEFAULT_WEEKLY = 8
PRICE_RUB = 2
STAR_RUB = 1.5

SUPPORT = "@bydeass"
OWNER = "@bydeass"

DB = "bot.db"
VIDEO_DIR = "videos"
BIN_DIR = "bin"

os.makedirs(VIDEO_DIR, exist_ok=True)
os.makedirs(BIN_DIR, exist_ok=True)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

video_queue = deque()
processing = False

# ================== FFMPEG PATH ==================

def get_ffmpeg_path():
    """Поиск ffmpeg в разных местах"""
    
    # 1. Сначала проверяем в ./bin/ffmpeg
    local_ffmpeg = os.path.join(BIN_DIR, 'ffmpeg')
    if os.path.exists(local_ffmpeg) and os.access(local_ffmpeg, os.X_OK):
        return local_ffmpeg
    
    # 2. Проверяем в текущей директории
    current_dir_ffmpeg = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ffmpeg')
    if os.path.exists(current_dir_ffmpeg) and os.access(current_dir_ffmpeg, os.X_OK):
        return current_dir_ffmpeg
    
    # 3. Проверяем системный ffmpeg
    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg:
        return system_ffmpeg
    
    return None

FFMPEG_PATH = get_ffmpeg_path()

# ================== DATABASE ==================

def db():
    return sqlite3.connect(DB)

def init_db():
    with db() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            circles INTEGER,
            weekly_limit INTEGER,
            week_start TEXT,
            referred_by INTEGER,
            referrals INTEGER DEFAULT 0,
            videos_done INTEGER DEFAULT 0
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_id TEXT PRIMARY KEY,
            user_id INTEGER,
            circles INTEGER,
            amount REAL,
            method TEXT,
            paid INTEGER
        )
        """)

def ensure_user(user):
    with db() as c:
        r = c.execute("SELECT 1 FROM users WHERE user_id=?", (user.id,)).fetchone()
        if not r:
            c.execute("""
            INSERT INTO users VALUES (?,?,?,?,?,?,?,?)
            """, (
                user.id, user.username,
                DEFAULT_WEEKLY, DEFAULT_WEEKLY,
                datetime.now().isoformat(),
                None, 0, 0
            ))

def get_limits(uid):
    with db() as c:
        circles, limit, week = c.execute(
            "SELECT circles, weekly_limit, week_start FROM users WHERE user_id=?",
            (uid,)
        ).fetchone()

        if datetime.now() - datetime.fromisoformat(week) > timedelta(days=7):
            circles = limit
            c.execute(
                "UPDATE users SET circles=?, week_start=? WHERE user_id=?",
                (circles, datetime.now().isoformat(), uid)
            )
        return circles, limit

def spend_circle(uid):
    with db() as c:
        c.execute("UPDATE users SET circles = circles - 1 WHERE user_id=?", (uid,))

def add_circles(uid, n):
    with db() as c:
        c.execute("UPDATE users SET circles = circles + ? WHERE user_id=?", (n, uid))

def get_user_by_username(username):
    with db() as c:
        return c.execute(
            "SELECT user_id FROM users WHERE username=?",
            (username.lstrip('@'),)
        ).fetchone()

def update_weekly_limit(uid, new_limit):
    with db() as c:
        c.execute(
            "UPDATE users SET weekly_limit = ? WHERE user_id=?",
            (new_limit, uid)
        )
        c.execute("""
        UPDATE users SET circles = ? 
        WHERE user_id=? AND circles < ?
        """, (new_limit, uid, new_limit))
        return True

# ================== REFERRALS ==================

def add_referral(new_id, ref_id):
    with db() as c:
        already = c.execute(
            "SELECT referred_by FROM users WHERE user_id=?", (new_id,)
        ).fetchone()
        if already and already[0] is not None:
            return False

        ref_exists = c.execute(
            "SELECT 1 FROM users WHERE user_id=?", (ref_id,)
        ).fetchone()

        if ref_exists and ref_id != new_id:
            c.execute(
                "UPDATE users SET referred_by=? WHERE user_id=?",
                (ref_id, new_id)
            )
            c.execute(
                "UPDATE users SET circles = circles + 1, referrals = referrals + 1 WHERE user_id=?",
                (ref_id,)
            )
            return True
    return False

def get_top_referrals():
    with db() as c:
        return c.execute("""
        SELECT user_id, username, referrals FROM users
        WHERE referrals > 0
        ORDER BY referrals DESC
        LIMIT 10
        """).fetchall()

# ================== KEYBOARDS ==================

def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Купить кружки", callback_data="buy")],
        [InlineKeyboardButton(text="👥 Топ рефералов", callback_data="top")],
        [InlineKeyboardButton(text="🔗 Поделиться", switch_inline_query="")]
    ])

def pay_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Stars", callback_data="stars")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main")]
    ])

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Выдать кружки", callback_data="give_circles")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="⚙️ Изменить лимит", callback_data="change_limit")]
    ])

def share_kb(ref_link):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 ОТПРАВИТЬ РЕФЕРАЛЬНУЮ ССЫЛКУ", switch_inline_query="")]
    ])

# ================== VIDEO CORE ==================

def ffmpeg_circle(inp, out):
    """Обработка видео с помощью ffmpeg"""
    global FFMPEG_PATH
    
    if FFMPEG_PATH is None:
        print("❌ FFMPEG не найден!")
        return False
    
    cmd = [
        FFMPEG_PATH, "-y",
        "-i", inp,
        "-vf", "crop=min(in_w\\,in_h):min(in_w\\,in_h),scale=640:640",
        "-t", "60",
        "-c:v", "libx264",
        "-profile:v", "baseline",
        "-level", "3.0",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        out
    ]
    
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Ошибка ffmpeg: {e}")
        return False
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

async def process_queue():
    global processing
    if processing:
        return
    processing = True

    while video_queue:
        m, user_id = video_queue.popleft()
        
        circles, _ = get_limits(user_id)
        if circles <= 0:
            await bot.send_message(user_id, "❌ Кружки закончились")
            continue

        wait_msg = await bot.send_message(user_id, "⏳ Видео в очереди, обрабатывается...")

        inp = f"{VIDEO_DIR}/in_{user_id}_{m.message_id}.mp4"
        out = f"{VIDEO_DIR}/out_{user_id}_{m.message_id}.mp4"

        try:
            # Скачиваем видео
            await bot.download(m.video, destination=inp)
            
            # Обрабатываем
            success = await asyncio.to_thread(ffmpeg_circle, inp, out)
            
            if not success or not os.path.exists(out) or os.path.getsize(out) == 0:
                await bot.send_message(user_id, "❌ Ошибка при обработке видео. Попробуйте другое видео.")
                continue

            # Отправляем результат
            await bot.send_video_note(user_id, FSInputFile(out))
            spend_circle(user_id)

            with db() as c:
                c.execute(
                    "UPDATE users SET videos_done = videos_done + 1 WHERE user_id=?",
                    (user_id,)
                )

            await wait_msg.edit_text("✅ Готово")

        except Exception as e:
            await bot.send_message(user_id, f"❌ Ошибка: {str(e)[:100]}")
            print(f"Error processing video: {e}")
        
        finally:
            # Очищаем временные файлы
            try:
                if os.path.exists(inp):
                    os.remove(inp)
                if os.path.exists(out):
                    os.remove(out)
            except:
                pass

    processing = False

@dp.message(F.content_type == ContentType.VIDEO)
async def handle_video(m: Message):
    ensure_user(m.from_user)
    
    # Проверяем наличие ffmpeg
    if FFMPEG_PATH is None:
        await m.answer("❌ Бот временно недоступен (ffmpeg не установлен). Администратор уже знает о проблеме.")
        await bot.send_message(ADMIN_ID, "⚠️ Ошибка: ffmpeg не найден! Запустите install_ffmpeg.sh")
        return
    
    video_queue.append((m, m.from_user.id))
    asyncio.create_task(process_queue())

# ================== BUY ==================

buy_cache = {}

@dp.callback_query(F.data == "buy")
async def buy(cb: CallbackQuery):
    await cb.message.edit_text("Сколько кружков нужно?", reply_markup=None)
    buy_cache[cb.from_user.id] = None

@dp.message(F.text.regexp(r"^\d+$"))
async def amount(m: Message):
    if m.from_user.id not in buy_cache:
        return
    n = int(m.text)
    buy_cache[m.from_user.id] = n
    price = n * PRICE_RUB
    stars = int(price / STAR_RUB)
    
    await m.answer(
        f"🛒 {n} кружков\n"
        f"💳 Цена: {price}₽\n"
        f"⭐ Stars: {stars} монет\n\n"
        f"Выберите способ оплаты:",
        reply_markup=pay_kb()
    )

# ================== STARS ==================

@dp.callback_query(F.data == "stars")
async def stars(cb: CallbackQuery):
    if cb.from_user.id not in buy_cache or buy_cache[cb.from_user.id] is None:
        await cb.answer("❌ Сначала укажите количество кружков")
        return
    
    n = buy_cache[cb.from_user.id]
    stars_amount = int((n * PRICE_RUB) / STAR_RUB)
    
    try:
        await bot.send_invoice(
            chat_id=cb.from_user.id,
            title="Кружки для видео",
            description=f"Покупка {n} кружков для обработки видео",
            payload=f"stars_{n}",
            provider_token="", 
            currency="XTR",
            prices=[LabeledPrice(label=f"{n} кружков", amount=stars_amount)]
        )
    except Exception as e:
        await cb.message.answer(f"❌ Ошибка создания счета Stars: {e}")

@dp.message(F.content_type == ContentType.SUCCESSFUL_PAYMENT)
async def stars_ok(m: Message):
    try:
        payload = m.successful_payment.invoice_payload
        if payload.startswith("stars_"):
            n = int(payload.split("_")[1])
            add_circles(m.from_user.id, n)
            await m.answer(f"✅ Начислено {n} кружков")
            await bot.send_message(ADMIN_ID, f"⭐ Stars: {n} кружков для {m.from_user.id}")
    except Exception as e:
        await bot.send_message(ADMIN_ID, f"❌ Ошибка Stars: {e}")

# ================== ADMIN ==================

@dp.message(Command("admin"))
async def admin(m: Message):
    if m.from_user.id == ADMIN_ID:
        await m.answer("⚙️ Админ меню", reply_markup=admin_kb())

@dp.callback_query(F.data == "stats")
async def stats(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
        
    with db() as c:
        users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        vids = c.execute("SELECT SUM(videos_done) FROM users").fetchone()[0] or 0
        total_circles = c.execute("SELECT SUM(circles) FROM users").fetchone()[0] or 0
        refs = c.execute("SELECT SUM(referrals) FROM users").fetchone()[0] or 0
        active_users = c.execute("SELECT COUNT(*) FROM users WHERE circles > 0").fetchone()[0]

    await cb.message.edit_text(
        f"📊 Статистика бота:\n\n"
        f"👥 Всего пользователей: {users}\n"
        f"🎯 Активных пользователей: {active_users}\n"
        f"🎥 Видео обработано: {vids}\n"
        f"💰 Кружков в системе: {total_circles}\n"
        f"👥 Рефералов всего: {refs}\n"
        f"🔧 FFmpeg: {'✅' if FFMPEG_PATH else '❌'}",
        reply_markup=admin_kb()
    )

@dp.callback_query(F.data == "give_circles")
async def give_circles_start(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
        
    await cb.message.edit_text(
        "🎁 Выдача кружков\n\n"
        "Отправьте сообщение в формате:\n"
        "<code>@username количество</code>\n\n"
        "Пример:\n"
        "<code>@bydeass 10</code>\n\n"
        "Или отправьте <code>cancel</code> для отмены.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_admin")]
        ])
    )

@dp.callback_query(F.data == "change_limit")
async def change_limit_start(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
        
    await cb.message.edit_text(
        "⚙️ Изменение еженедельного лимита\n\n"
        "Отправьте сообщение в формате:\n"
        "<code>@username новый_лимит</code>\n\n"
        "Пример:\n"
        "<code>@bydeass 20</code>\n\n"
        "Или отправьте <code>cancel</code> для отмены.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_admin")]
        ])
    )

@dp.callback_query(F.data == "back_admin")
async def back_admin(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID:
        return
    await cb.message.edit_text("⚙️ Админ меню", reply_markup=admin_kb())

@dp.message(F.text.regexp(r"^@\w+\s+\d+$"))
async def handle_admin_command(m: Message):
    if m.from_user.id != ADMIN_ID:
        return
        
    parts = m.text.split()
    username = parts[0].lstrip('@')
    amount = int(parts[1])
    
    user_data = get_user_by_username(username)
    if not user_data:
        await m.answer(f"❌ Пользователь @{username} не найден")
        return
    
    user_id = user_data[0]
    
    if m.reply_to_message and "изменить лимит" in m.reply_to_message.text.lower():
        update_weekly_limit(user_id, amount)
        await m.answer(f"✅ Лимит пользователя @{username} изменен на {amount}")
        await bot.send_message(user_id, f"⚙️ Ваш еженедельный лимит изменен на {amount} кружков!")
    else:
        add_circles(user_id, amount)
        await m.answer(f"✅ Выдано {amount} кружков пользователю @{username}")
        await bot.send_message(user_id, f"🎁 Администратор выдал вам {amount} кружков!")

@dp.message(F.text.lower() == "cancel")
async def cancel_command(m: Message):
    if m.from_user.id == ADMIN_ID:
        await m.answer("❌ Операция отменена", reply_markup=admin_kb())

# ================== INLINE QUERY ==================

@dp.inline_query()
async def inline_query_handler(query: InlineQuery):
    me = await bot.get_me()
    ref_link = f"https://t.me/{me.username}?start={query.from_user.id}"
    
    results = [
        InlineQueryResultArticle(
            id="1",
            title="🔗 Поделиться реферальной ссылкой",
            description="Нажмите, чтобы отправить свою реферальную ссылку",
            input_message_content=InputTextMessageContent(
                message_text=f"🎥 Привет! Попробуй этого бота для создания крутых видео!\n\n"
                           f"🔗 {ref_link}\n\n"
                           f"🎁 Получи +1 кружок за каждого друга!",
                parse_mode=None
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✨ Попробовать", url=ref_link)]
            ])
        )
    ]
    
    await query.answer(results, cache_time=1, is_personal=True)

@dp.chosen_inline_result()
async def chosen_inline_result(chosen_result: ChosenInlineResult):
    print(f"User {chosen_result.from_user.id} used inline query")

# ================== SHARE COMMAND ==================

@dp.message(Command("share"))
async def share_command(m: Message):
    me = await bot.get_me()
    ref_link = f"https://t.me/{me.username}?start={m.from_user.id}"
    
    await m.answer(
        f"📤 Поделитесь реферальной ссылкой и получайте кружки!\n\n"
        f"👥 1 друг = 1 кружок\n\n"
        f"🔗 Ваша ссылка:\n{ref_link}",
        reply_markup=share_kb(ref_link)
    )

# ================== MENTION HANDLER ==================

@dp.message(F.text == "@create_funny_bot")
async def mention_handler(m: Message):
    me = await bot.get_me()
    ref_link = f"https://t.me/{me.username}?start={m.from_user.id}"
    
    circles, limit = get_limits(m.from_user.id)
    
    await m.answer(
        f"🤖 *create_funny_bot*\n\n"
        f"✨ Функции бота:\n"
        f"• 🎥 Обработка видео в кружки\n"
        f"• 🔄 Автоматическое обновление кружков\n"
        f"• 👥 Реферальная система\n\n"
        f"📊 Ваша статистика:\n"
        f"• 🎥 Кружки: {circles}/{limit}\n\n"
        f"🔗 Ваша реферальная ссылка:\n"
        f"{ref_link}\n\n"
        f"Поделитесь ссылкой и получайте +1 кружок за каждого друга!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 ОТПРАВИТЬ РЕФЕРАЛЬНУЮ ССЫЛКУ", switch_inline_query="")],
            [InlineKeyboardButton(text="👥 Топ рефералов", callback_data="top")],
            [InlineKeyboardButton(text="💰 Купить кружки", callback_data="buy")]
        ])
    )

# ================== HELP COMMAND ==================

@dp.message(Command("help"))
async def help_command(m: Message):
    await m.answer(
        "🆘 *Помощь по боту*\n\n"
        "📖 *Основные команды:*\n"
        "/start - Запустить бота\n"
        "/help - Помощь\n"
        "/share - Реферальная ссылка\n\n"
        "🎥 *Как использовать:*\n"
        "1. Отправьте видео боту\n"
        "2. Бот обработает его в кружок\n"
        "3. Используется 1 кружок за обработку\n\n"
        "👥 *Реферальная система:*\n"
        "• 1 друг = 1 кружок\n"
        "• Используйте кнопку 'Поделиться'\n\n"
        "💰 *Покупка кружков:*\n"
        "• Нажмите 'Купить кружки'\n"
        "• Выберите способ оплаты\n\n"
        f"💬 *Поддержка:* {SUPPORT}",
        parse_mode="Markdown",
        reply_markup=main_kb()
    )

# ================== ERROR HANDLER ==================

@dp.errors()
async def error_handler(event, exception):
    print(f"Error occurred: {exception}")
    
    try:
        await bot.send_message(
            ADMIN_ID,
            f"❌ Ошибка в боте:\n\n"
            f"```{str(exception)[:1000]}```\n\n"
            f"Тип: {type(exception).__name__}",
            parse_mode="Markdown"
        )
    except:
        pass
    
    return True

# ================== RUN ==================

async def main():
    # Проверка ffmpeg
    global FFMPEG_PATH
    FFMPEG_PATH = get_ffmpeg_path()
    
    print("🤖 Бот запускается...")
    print(f"📁 Директория для видео: {VIDEO_DIR}")
    print(f"💾 База данных: {DB}")
    
    if FFMPEG_PATH:
        print(f"✅ FFmpeg найден: {FFMPEG_PATH}")
    else:
        print("❌ FFmpeg НЕ НАЙДЕН!")
        print("📥 Загрузите ffmpeg вручную или запустите install_ffmpeg.sh")
        print("   Команда: bash install_ffmpeg.sh")
    
    # Инициализация БД
    init_db()
    
    # Очистка старых временных файлов
    for f in os.listdir(VIDEO_DIR):
        try:
            os.remove(os.path.join(VIDEO_DIR, f))
        except:
            pass
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
