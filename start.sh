#!/bin/bash

# Установка ffmpeg если не установлен
if ! command -v ffmpeg &> /dev/null; then
    echo "Устанавливаем ffmpeg..."
    apt-get update
    apt-get install -y ffmpeg
fi

# Создаем директорию для видео
mkdir -p videos

# Запуск бота
python3 main.py
