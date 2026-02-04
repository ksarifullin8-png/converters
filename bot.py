import os
import logging
import asyncio
import subprocess
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, FSInputFile, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ========== НАСТРОЙКИ ==========
# Токен берется из переменных окружения Render
API_TOKEN = os.getenv("BOT_TOKEN")

if not API_TOKEN:
    raise ValueError("❌ BOT_TOKEN не установлен! Добавьте его в настройки Render.")

# Папка для временных файлов
TEMP_DIR = Path("temp_files")
TEMP_DIR.mkdir(exist_ok=True)

# Максимальный размер файла (50 MB)
MAX_FILE_SIZE = 50 * 1024 * 1024

# ========== НАСТРОЙКА ЛОГГИРОВАНИЯ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ========== ПРОВЕРКА FFMPEG ==========
def check_ffmpeg() -> bool:
    """Проверяет, установлен ли ffmpeg."""
    try:
        subprocess.run(['ffmpeg', '-version'], 
                      capture_output=True, 
                      check=True)
        logger.info("✅ FFmpeg найден и работает")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error(f"❌ FFmpeg не найден: {e}")
        return False

# Проверяем при запуске
if not check_ffmpeg():
    logger.warning("FFmpeg не найден. Установите его в Render через apt-get.")

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def run_ffmpeg_command(cmd: list) -> bool:
    """Запускает команду ffmpeg."""
    try:
        logger.info(f"Запуск FFmpeg: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        logger.info("✅ FFmpeg выполнился успешно")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Ошибка FFmpeg (код {e.returncode}): {e.stderr[:200]}")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка запуска FFmpeg: {e}")
        return False

async def convert_to_video_note(input_path: Path, output_path: Path) -> Optional[Path]:
    """Конвертирует в видеосообщение (кружок)."""
    cmd = [
        'ffmpeg',
        '-i', str(input_path),
        '-vf', r'crop=min(iw\,ih):min(iw\,ih),scale=720:720,setsar=1',
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '28',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-t', '60',
        '-y',
        str(output_path)
    ]
    
    return output_path if run_ffmpeg_command(cmd) else None

async def convert_to_voice(input_path: Path, output_path: Path) -> Optional[Path]:
    """Извлекает аудио для голосового сообщения."""
    cmd = [
        'ffmpeg',
        '-i', str(input_path),
        '-vn',
        '-c:a', 'libopus',
        '-b:a', '64k',
        '-f', 'ogg',
        '-y',
        str(output_path)
    ]
    
    return output_path if run_ffmpeg_command(cmd) else None

async def cleanup_files(*files):
    """Удаляет временные файлы."""
    for file in files:
        if file and file.exists():
            try:
                file.unlink()
                logger.info(f"🗑️ Удален: {file}")
            except Exception as e:
                logger.error(f"Ошибка удаления {file}: {e}")

# Хранилище данных пользователей (в памяти)
user_data = {}

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "🤖 <b>Привет! Я бот-конвертер на Render!</b>\n\n"
        "Отправьте мне видео, и я могу:\n"
        "1. Сделать <b>видеосообщение (кружок)</b>\n"
        "2. Извлечь <b>аудио (голосовое сообщение)</b>\n\n"
        "Просто отправьте видео файлом! 🎬"
    )

@router.message(Command("status"))
async def cmd_status(message: Message):
    """Проверка статуса бота."""
    status = "✅ Бот работает исправно\n"
    status += f"👤 Пользователей в памяти: {len(user_data)}\n"
    status += f"📂 Временных файлов: {len(list(TEMP_DIR.glob('*')))}\n"
    
    # Проверка FFmpeg
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        status += "🔧 FFmpeg: ✅ установлен\n"
    except:
        status += "🔧 FFmpeg: ❌ не найден\n"
    
    await message.answer(status)

@router.message(F.video | F.document)
async def handle_video(message: Message):
    """Получаем видео от пользователя."""
    user_id = message.from_user.id
    
    # Определяем тип файла
    if message.video:
        file_info = {
            'file_id': message.video.file_id,
            'file_name': message.video.file_name or "video.mp4",
            'file_size': message.video.file_size
        }
    elif message.document:
        mime_type = message.document.mime_type or ''
        if 'video' in mime_type:
            file_info = {
                'file_id': message.document.file_id,
                'file_name': message.document.file_name or "video",
                'file_size': message.document.file_size
            }
        else:
            await message.answer("❌ Пожалуйста, отправьте видеофайл.")
            return
    
    # Проверка размера
    if file_info.get('file_size', 0) > MAX_FILE_SIZE:
        await message.answer(f"❌ Файл слишком большой! Максимум: {MAX_FILE_SIZE // (1024*1024)}MB")
        return
    
    # Создаем меню выбора
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎥 Сделать видеосообщение")],
            [KeyboardButton(text="🎵 Извлечь аудио")],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    
    # Сохраняем данные
    user_data[user_id] = file_info
    await message.answer(
        f"✅ <b>Видео получено!</b>\n"
        f"📁 Файл: <code>{file_info['file_name']}</code>\n"
        f"📊 Размер: {file_info.get('file_size', 0) // 1024} KB\n\n"
        f"Выберите действие:",
        reply_markup=keyboard
    )

@router.message(F.text == "🎥 Сделать видеосообщение")
async def make_video_note(message: Message):
    """Обрабатываем создание видеосообщения."""
    user_id = message.from_user.id
    
    if user_id not in user_data:
        await message.answer("❌ Сначала отправьте видео!", reply_markup=ReplyKeyboardRemove())
        return
    
    data = user_data[user_id]
    msg_wait = await message.answer("⏳ Скачиваю и обрабатываю видео...")
    
    try:
        # Скачиваем файл
        file = await bot.get_file(data['file_id'])
        download_path = TEMP_DIR / f"dl_{user_id}_{message.message_id}.mp4"
        await bot.download_file(file.file_path, download_path)
        
        # Конвертируем
        output_path = TEMP_DIR / f"vn_{user_id}_{message.message_id}.mp4"
        result = await convert_to_video_note(download_path, output_path)
        
        if result and result.exists():
            # Отправляем результат
            await message.answer_video_note(FSInputFile(result))
            await msg_wait.edit_text("✅ Видеосообщение отправлено!")
        else:
            await msg_wait.edit_text("❌ Ошибка конвертации. Проверьте формат видео.")
        
        # Очистка
        await cleanup_files(download_path, result)
        
    except Exception as e:
        logger.error(f"Ошибка в make_video_note: {e}")
        await msg_wait.edit_text("❌ Произошла ошибка при обработке.")
    finally:
        user_data.pop(user_id, None)
        await message.answer("🔄 Готов к новой задаче!", reply_markup=ReplyKeyboardRemove())

@router.message(F.text == "🎵 Извлечь аудио")
async def make_voice(message: Message):
    """Обрабатываем извлечение аудио."""
    user_id = message.from_user.id
    
    if user_id not in user_data:
        await message.answer("❌ Сначала отправьте видео!", reply_markup=ReplyKeyboardRemove())
        return
    
    data = user_data[user_id]
    msg_wait = await message.answer("⏳ Извлекаю аудио...")
    
    try:
        # Скачиваем файл
        file = await bot.get_file(data['file_id'])
        download_path = TEMP_DIR / f"dl_{user_id}_{message.message_id}.mp4"
        await bot.download_file(file.file_path, download_path)
        
        # Конвертируем
        output_path = TEMP_DIR / f"vc_{user_id}_{message.message_id}.ogg"
        result = await convert_to_voice(download_path, output_path)
        
        if result and result.exists():
            # Отправляем результат
            await message.answer_voice(FSInputFile(result))
            await msg_wait.edit_text("✅ Голосовое сообщение отправлено!")
        else:
            await msg_wait.edit_text("❌ Ошибка конвертации.")
        
        # Очистка
        await cleanup_files(download_path, result)
        
    except Exception as e:
        logger.error(f"Ошибка в make_voice: {e}")
        await msg_wait.edit_text("❌ Произошла ошибка при обработке.")
    finally:
        user_data.pop(user_id, None)
        await message.answer("🔄 Готов к новой задаче!", reply_markup=ReplyKeyboardRemove())

@router.message(F.text == "❌ Отмена")
async def cancel_action(message: Message):
    """Отмена текущей операции."""
    user_id = message.from_user.id
    user_data.pop(user_id, None)
    await message.answer("❌ Действие отменено.", reply_markup=ReplyKeyboardRemove())

@router.message()
async def unknown_message(message: Message):
    """Обработка неизвестных сообщений."""
    await message.answer(
        "🤔 Я не понял команду.\n"
        "Отправьте /start для начала работы\n"
        "Отправьте /status для проверки состояния бота"
    )

# ========== ЗАПУСК БОТА ==========
async def main():
    """Основная функция запуска."""
    logger.info("🚀 Запуск Telegram бота на Render...")
    logger.info(f"🤖 ID бота: {(await bot.get_me()).id}")
    logger.info(f"📂 Рабочая директория: {Path.cwd()}")
    
    # Очистка старых временных файлов
    for file in TEMP_DIR.glob("*"):
        try:
            file.unlink()
        except:
            pass
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Запускаем бота
    asyncio.run(main())
