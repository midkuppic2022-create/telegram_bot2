#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  printf 'Usage: %s backups/inspections_YYYYMMDD_HHMMSS.sql.gz\n' "$0" >&2
  exit 2
fi

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SOURCE=$1

cd "$PROJECT_DIR"
set -a
. ./.env
set +a

gzip -dc "$SOURCE" | docker compose exec -T db psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB"

