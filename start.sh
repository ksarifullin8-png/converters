#!/bin/bash

# Установка ffmpeg если не установлен
if [ ! -f "bin/ffmpeg" ]; then
    echo "🔄 Установка ffmpeg..."
    bash install_ffmpeg.sh
fi

# Запуск бота
echo "🚀 Запуск бота..."
python3 main.py
