#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ENV_FILE_PATH=${1:-.env.smoke}
PROJECT_NAME=${SMOKE_PROJECT_NAME:-inspections-smoke}

cd "$PROJECT_DIR"
if [ ! -f "$ENV_FILE_PATH" ]; then
  printf 'Smoke environment file not found: %s\n' "$ENV_FILE_PATH" >&2
  printf 'Copy .env.smoke.example and add a separate Telegram test token.\n' >&2
  exit 2
fi

compose() {
  ENV_FILE="$ENV_FILE_PATH" docker compose \
    --env-file "$ENV_FILE_PATH" \
    -p "$PROJECT_NAME" \
    "$@"
}

cleanup() {
  compose down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

compose up --build -d db redis
compose run --rm migrate
compose up --build -d --no-deps bot

compose exec -T db sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
compose exec -T redis redis-cli ping
compose run --rm migrate /app/.venv/bin/alembic current

BOT_CONTAINER=$(compose ps -q bot)
BOT_READY=0
ATTEMPT=0
while [ "$ATTEMPT" -lt 30 ]; do
  if [ -n "$BOT_CONTAINER" ]; then
    if [ "$(docker inspect -f '{{.State.Running}}' "$BOT_CONTAINER")" != "true" ]; then
      compose logs --no-color bot
      printf 'Bot container stopped before long polling started.\n' >&2
      exit 1
    fi
    BOT_HEALTH=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$BOT_CONTAINER")
    if [ "$BOT_HEALTH" = "healthy" ] && \
      compose logs --no-color bot 2>&1 | grep -q 'Starting bot in long-polling mode'; then
      BOT_READY=1
      break
    fi
  fi
  ATTEMPT=$((ATTEMPT + 1))
  sleep 2
  BOT_CONTAINER=$(compose ps -q bot)
done

if [ "$BOT_READY" != "1" ]; then
  compose logs bot
  printf 'Bot did not become healthy and start long polling within 60 seconds.\n' >&2
  exit 1
fi

compose ps
printf 'Clean Docker smoke test passed for project %s.\n' "$PROJECT_NAME"
