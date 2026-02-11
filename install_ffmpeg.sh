#!/bin/bash

echo "🔄 Установка ffmpeg для BotHost (бесплатный тариф)..."

# Создаем директорию для бинарников
mkdir -p bin
cd bin

# Скачиваем статическую сборку ffmpeg (маленький размер)
echo "📥 Скачивание ffmpeg..."
wget -q https://github.com/eugeneware/ffmpeg-static/releases/download/b4.4/ffmpeg-linux-x64 -O ffmpeg

# Делаем исполняемым
chmod +x ffmpeg

cd ..

echo "✅ FFmpeg установлен в ./bin/ffmpeg"
./bin/ffmpeg -version | head -n1

echo "✅ Готово!"
