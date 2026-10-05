#!/bin/bash
set -e

# === НАСТРОЙКИ ===
NEW_VPS_IP="132.243.227.202"
NEW_VPS_USER="root"

PROJECT_DIR="/opt/inspection-bot"
BACKUP_DIR="${PROJECT_DIR}/tmp_migration"
# =================

echo "=== 1. Подготовка папок ==="
mkdir -p "${BACKUP_DIR}"
cd "${PROJECT_DIR}"

echo "=== 2. Снятие дампа PostgreSQL ==="
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
else
    echo "Ошибка: Файл .env не найден в ${PROJECT_DIR}!"
    exit 1
fi

docker compose exec -T db pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" > "${BACKUP_DIR}/db_dump.sql"
echo "Дамп PostgreSQL успешно создан."

echo "=== 3. Снятие дампа Redis ==="
docker compose exec -T redis redis-cli save || true
docker cp $(docker compose ps -q redis):/data/dump.rdb "${BACKUP_DIR}/redis_dump.rdb" || true
echo "Дамп Redis сохранен."

echo "=== 4. Остановка бота на старом VPS ==="
docker compose down

echo "=== 5. Упаковка проекта в архив ==="
cd /opt
tar --exclude='inspection-bot/backups' \
    --exclude='inspection-bot/tmp' \
    --exclude='inspection-bot/.venv' \
    --exclude='inspection-bot/__pycache__' \
    -czvf inspection-bot_full.tar.gz inspection-bot/

echo "=== 6. Передача архива на новый VPS ==="
echo "Введите пароль от нового VPS для отправки файла:"
scp /opt/inspection-bot_full.tar.gz ${NEW_VPS_USER}@${NEW_VPS_IP}:/opt/

echo "=== 7. Автоматическое развертывание на новом VPS ==="
echo "Введите пароль от нового VPS еще раз для запуска развертывания:"

ssh -t ${NEW_VPS_USER}@${NEW_VPS_IP} "bash -s" << 'EOF'
set -e

echo "--> [Новый VPS] 1. Установка Docker (если не установлен)..."
if ! command -v docker &> /dev/null; then
    apt update && apt install -y docker.io docker-compose-v2
fi

echo "--> [Новый VPS] 2. Распаковка архива..."
cd /opt
tar -xzvf inspection-bot_full.tar.gz
cd /opt/inspection-bot

echo "--> [Новый VPS] 3. Запуск баз данных..."
docker compose up -d db redis
sleep 5

echo "--> [Новый VPS] 4. Восстановление БД PostgreSQL..."
export $(grep -v '^#' .env | xargs)
cat tmp_migration/db_dump.sql | docker compose exec -T db psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}"

echo "--> [Новый VPS] 5. Восстановление Redis..."
if [ -f tmp_migration/redis_dump.rdb ]; then
    docker compose stop redis
    docker cp tmp_migration/redis_dump.rdb $(docker compose ps -aq redis):/data/dump.rdb
    docker compose start redis
fi

echo "--> [Новый VPS] 6. Очистка временных файлов и запуск всего проекта..."
rm -rf tmp_migration
docker compose up -d --build

echo "--> [Новый VPS] Развертывание успешно завершено!"
EOF

echo "=== ПЕРЕНОС ПОЛНОСТЬЮ ЗАВЕРШЕН ==="