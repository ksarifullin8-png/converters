#!/bin/bash
echo "🚀 Установка зависимостей..."
pip install -r requirements.txt

echo "🔧 Проверка FFmpeg..."
if ! command -v ffmpeg &> /dev/null; then
    echo "❌ FFmpeg не найден. Установка..."
    apt-get update && apt-get install -y ffmpeg
fi

echo "✅ Запуск бота..."
python bot.py
