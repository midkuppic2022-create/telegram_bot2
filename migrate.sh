#!/bin/bash
set -e

echo "=== Запуск миграций Alembic ==="
alembic upgrade head
echo "=== Миграции успешно применены ==="