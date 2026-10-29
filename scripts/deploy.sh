#!/usr/bin/env bash
# Deploy a released tag on the VDS. Invoked over SSH by GitHub Actions.
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/tg-bot-eng-teacher}"
TAG_PATTERN='^v[0-9]+\.[0-9]+\.[0-9]+$'

original="${SSH_ORIGINAL_COMMAND:-}"
read -r -a parts <<< "$original"
tag="${parts[1]:-}"

if [[ ! "$tag" =~ $TAG_PATTERN ]]; then
  echo "usage: deploy vX.Y.Z (got: '$original')" >&2
  exit 64
fi

cd "$APP_DIR"

if [[ ! -f .env ]]; then
  echo "missing $APP_DIR/.env" >&2
  exit 1
fi

echo "==> fetch $tag"
git fetch --tags --force origin
git checkout --force "$tag"

if [[ -f data/bot.db ]]; then
  mkdir -p backups
  backup="backups/pre-release-$(date +%Y%m%d-%H%M%S).db"
  echo "==> backup $backup"
  sqlite3 data/bot.db ".backup '$backup'"
  ls -1t backups/*.db | tail -n +11 | xargs -r rm -f
fi

echo "==> build"
docker compose build

echo "==> start"
docker compose up -d --remove-orphans

container="$(docker compose ps -q bot)"
if [[ -z "$container" ]]; then
  echo "bot container not found" >&2
  exit 1
fi

for _ in $(seq 1 30); do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$container")" == "true" ]]; then
    break
  fi
  sleep 2
done

if [[ "$(docker inspect -f '{{.State.Running}}' "$container")" != "true" ]]; then
  docker compose logs --tail=100 bot >&2
  echo "bot is not running after deploy" >&2
  exit 1
fi

docker image prune -f >/dev/null
docker compose ps
echo "==> deployed $tag"
