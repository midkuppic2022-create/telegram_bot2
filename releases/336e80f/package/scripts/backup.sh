#!/bin/sh
set -eu
umask 077

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BACKUP_DIR=${BACKUP_DIR:-"$PROJECT_DIR/backups"}
KEEP_DAYS=${KEEP_DAYS:-7}

cd "$PROJECT_DIR"
set -a
. ./.env
set +a

mkdir -p "$BACKUP_DIR"
STAMP=$(date +%Y%m%d_%H%M%S)
TARGET="$BACKUP_DIR/inspections_$STAMP.sql.gz"

docker compose exec -T db pg_dump \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --clean --if-exists --no-owner | gzip > "$TARGET"

find "$BACKUP_DIR" -type f -name 'inspections_*.sql.gz' -mtime "+$KEEP_DAYS" -delete
printf '%s\n' "$TARGET"
