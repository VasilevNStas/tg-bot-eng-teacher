#!/usr/bin/env bash
# Database lifecycle helper for the bot.
#
# Defaults to Docker Compose (the production-like path). Use --local to run
# migrations through `uv` and skip container stop/start.
#
#   ./scripts/db.sh status
#   ./scripts/db.sh backup
#   ./scripts/db.sh migrate
#   ./scripts/db.sh reset --yes
#   ./scripts/db.sh restore backups/pre-reset-20261103-120000-1234.db
#   ./scripts/db.sh prune
#
# The database file is never touched automatically at startup. `reset` and
# `restore` are destructive and always require an explicit command.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DB_PATH="${DB_PATH:-$ROOT_DIR/data/bot.db}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
BACKUP_RETENTION="${BACKUP_RETENTION:-10}"
COMPOSE="${COMPOSE:-docker compose}"
DRILL_LOG="${DRILL_LOG:-}"
LOCAL=0

usage() {
  cat <<'EOF'
Usage: ./scripts/db.sh [--local] <command> [options]

Commands:
  status                 Show path, size, journal mode, revision and row counts.
  backup [prefix]        Online SQLite backup into backups/ (keeps last 10).
  migrate [--no-backup]  Stop, back up, upgrade schema, seed, start.
  reset --yes [--force]  Stop, back up, delete the DB (+ WAL/SHM), start.
        [--no-backup] [--no-start]
  restore <file>         Stop, verify, back up current DB, replace, start.
        [--no-backup] [--no-start]
  prune                  Deactivate content items missing from bundled YAML.
  drill [file]           Restore a backup into a temp dir and verify it.

Global options:
  --local    Use uv/alembic directly instead of Docker Compose.
  -h, --help Show this help.

Environment:
  DB_PATH              default: ./data/bot.db
  BACKUP_DIR           default: ./backups
  BACKUP_RETENTION     default: 10
  COMPOSE              default: "docker compose"
  APP_ENV=production   makes `reset` require an extra --force.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '==> %s\n' "$*" >&2
}

is_local() {
  [ "$LOCAL" -eq 1 ]
}

require_sqlite3() {
  command -v sqlite3 >/dev/null 2>&1 || die "sqlite3 is required but not installed"
}

stop_app() {
  if is_local; then
    info "local mode: make sure the bot process is stopped"
    return 0
  fi
  $COMPOSE stop bot >/dev/null 2>&1 || true
}

start_app() {
  if is_local; then
    info "local mode: start with 'uv run python -m app'"
    return 0
  fi
  $COMPOSE up -d
}

prune_backups() {
  local count=0 file
  while IFS= read -r file; do
    count=$((count + 1))
    if [ "$count" -gt "$BACKUP_RETENTION" ]; then
      rm -f "$file"
    fi
  done < <(ls -1t "$BACKUP_DIR"/*.db 2>/dev/null || true)
}

backup_db() {
  local prefix="${1:-manual}"
  local stamp out
  require_sqlite3
  [ -f "$DB_PATH" ] || die "database not found: $DB_PATH"
  mkdir -p "$BACKUP_DIR"
  stamp="$(date +%Y%m%d-%H%M%S)"
  out="$BACKUP_DIR/${prefix}-${stamp}-$$-${RANDOM}.db"
  info "backup $DB_PATH -> $out"
  sqlite3 "$DB_PATH" ".backup '$out'"
  prune_backups
  printf '%s\n' "$out"
}

check_integrity() {
  local file="$1" result
  require_sqlite3
  result="$(sqlite3 "$file" 'PRAGMA integrity_check;' 2>&1 || true)"
  [ "$result" = "ok" ] || die "integrity check failed for $file: $result"
}

cmd_status() {
  [ "$#" -eq 0 ] || die "status takes no arguments"

  printf 'database: %s\n' "$DB_PATH"
  if [ ! -f "$DB_PATH" ]; then
    printf 'status: missing\n'
    return 0
  fi

  printf 'size: %s bytes\n' "$(wc -c < "$DB_PATH" | tr -d ' ')"
  [ -f "$DB_PATH-wal" ] && printf 'wal: present\n'
  [ -f "$DB_PATH-shm" ] && printf 'shm: present\n'

  if ! command -v sqlite3 >/dev/null 2>&1; then
    printf 'sqlite3: not installed (details unavailable)\n'
    return 0
  fi

  printf 'journal_mode: %s\n' "$(sqlite3 "$DB_PATH" 'PRAGMA journal_mode;' 2>/dev/null || printf 'n/a')"
  printf 'integrity: %s\n' "$(sqlite3 "$DB_PATH" 'PRAGMA integrity_check;' 2>/dev/null || printf 'n/a')"
  printf 'revision: %s\n' "$(sqlite3 "$DB_PATH" 'SELECT version_num FROM alembic_version;' 2>/dev/null || printf 'n/a')"
  printf '\n%-20s %s\n' "table" "rows"
  local table rows
  for table in users conversations messages content_items vocabulary word_progress exercise_sessions usage_ledger; do
    rows="$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM $table;" 2>/dev/null || printf 'n/a')"
    printf '%-20s %s\n' "$table" "$rows"
  done
}

cmd_backup() {
  [ "$#" -le 1 ] || die "usage: db.sh backup [prefix]"
  backup_db "${1:-manual}"
}

cmd_migrate() {
  local no_backup=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --no-backup) no_backup=1 ;;
      *) die "unknown option for migrate: $1" ;;
    esac
    shift
  done

  stop_app
  if [ -f "$DB_PATH" ] && [ "$no_backup" -eq 0 ]; then
    backup_db pre-migrate >/dev/null
  fi

  info "apply migrations"
  if is_local; then
    uv run alembic upgrade head
    info "seed content"
    uv run python -m app.cli seed
  else
    $COMPOSE run --rm --no-deps bot alembic upgrade head
    $COMPOSE run --rm --no-deps bot python -m app.cli seed
  fi

  start_app
}

cmd_reset() {
  local yes=0 force=0 no_backup=0 no_start=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --yes) yes=1 ;;
      --force) force=1 ;;
      --no-backup) no_backup=1 ;;
      --no-start) no_start=1 ;;
      *) die "unknown option for reset: $1" ;;
    esac
    shift
  done

  case "$DB_PATH" in
    *.db) ;;
    *) die "DB_PATH must point to a .db file: $DB_PATH" ;;
  esac
  [ -d "$DB_PATH" ] && die "DB_PATH is a directory: $DB_PATH"
  [ "$yes" -eq 1 ] || die "refusing to reset without --yes"
  if [ "${APP_ENV:-}" = "production" ] && [ "$force" -ne 1 ]; then
    die "refusing to reset production data; pass --force to override"
  fi

  stop_app
  if [ -f "$DB_PATH" ] && [ "$no_backup" -eq 0 ]; then
    backup_db pre-reset >/dev/null
  fi

  info "remove $DB_PATH (and WAL/SHM)"
  rm -f "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm"

  if [ "$no_start" -eq 0 ]; then
    start_app
  fi
}

cmd_restore() {
  local source="${1:-}"
  [ -n "$source" ] || die "usage: db.sh restore <file>"
  shift

  local no_backup=0 no_start=0
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --no-backup) no_backup=1 ;;
      --no-start) no_start=1 ;;
      *) die "unknown option for restore: $1" ;;
    esac
    shift
  done

  [ -f "$source" ] || die "backup file not found: $source"
  [ "$(cd "$(dirname "$source")" && pwd)/$(basename "$source")" != "$DB_PATH" ] \
    || die "source and target are the same file"

  info "verify $source"
  check_integrity "$source"

  stop_app
  if [ -f "$DB_PATH" ] && [ "$no_backup" -eq 0 ]; then
    backup_db pre-restore >/dev/null
  fi

  info "restore $source -> $DB_PATH"
  rm -f "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm"
  mkdir -p "$(dirname "$DB_PATH")"
  cp "$source" "$DB_PATH"

  if [ "$no_start" -eq 0 ]; then
    start_app
  fi
}

cmd_prune() {
  [ "$#" -eq 0 ] || die "prune takes no arguments"
  info "deactivate content missing from bundled YAML"
  if is_local; then
    uv run python -m app.cli seed --prune
  else
    $COMPOSE run --rm --no-deps bot python -m app.cli seed --prune
  fi
}

latest_backup() {
  local file
  file="$(ls -1t "$BACKUP_DIR"/*.db 2>/dev/null | head -n 1 || true)"
  printf '%s' "$file"
}

count_rows() {
  local db="$1" table="$2"
  sqlite3 "$db" "SELECT COUNT(*) FROM $table;" 2>/dev/null || printf 'n/a'
}

cmd_drill() {
  local source=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -h|--help) usage; return 0 ;;
      -*) die "unknown option for drill: $1" ;;
      *)
        [ -z "$source" ] || die "usage: db.sh drill [file]"
        source="$1"
        ;;
    esac
    shift
  done

  if [ -z "$source" ]; then
    source="$(latest_backup)"
    [ -n "$source" ] || die "no backups found in $BACKUP_DIR"
  fi
  [ -f "$source" ] || die "backup file not found: $source"

  local work
  work="$(mktemp -d "${TMPDIR:-/tmp}/db-drill.XXXXXX")"
  # shellcheck disable=SC2064
  trap "rm -rf '$work'" EXIT

  info "drill $source"
  cp "$source" "$work/bot.db"
  check_integrity "$work/bot.db"

  local revision
  revision="$(sqlite3 "$work/bot.db" 'SELECT version_num FROM alembic_version;' 2>/dev/null || true)"
  [ -n "$revision" ] || die "backup has no alembic_version: $source"

  local required="users content_items exercise_sessions word_progress"
  local table missing=""
  for table in $required; do
    if [ -z "$(sqlite3 "$work/bot.db" "SELECT name FROM sqlite_master WHERE type='table' AND name='$table';" 2>/dev/null)" ]; then
      missing="$missing $table"
    fi
  done
  [ -z "$missing" ] || die "backup is missing tables:$missing"

  local users items sessions progress
  users="$(count_rows "$work/bot.db" users)"
  items="$(count_rows "$work/bot.db" content_items)"
  sessions="$(count_rows "$work/bot.db" exercise_sessions)"
  progress="$(count_rows "$work/bot.db" word_progress)"

  [ -n "$DRILL_LOG" ] || DRILL_LOG="$BACKUP_DIR/drills.log"
  mkdir -p "$(dirname "$DRILL_LOG")"
  printf '%s ok source=%s revision=%s users=%s content_items=%s sessions=%s word_progress=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$source" "$revision" \
    "$users" "$items" "$sessions" "$progress" >> "$DRILL_LOG"

  printf 'source: %s\n' "$source"
  printf 'integrity: ok\n'
  printf 'revision: %s\n' "$revision"
  printf 'users: %s\n' "$users"
  printf 'content_items: %s\n' "$items"
  printf 'exercise_sessions: %s\n' "$sessions"
  printf 'word_progress: %s\n' "$progress"
  printf 'log: %s\n' "$DRILL_LOG"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --local) LOCAL=1 ;;
    -h|--help) usage; exit 0 ;;
    *) break ;;
  esac
  shift
done

command="${1:-}"
[ -n "$command" ] || { usage >&2; exit 2; }
shift

case "$command" in
  status) cmd_status "$@" ;;
  backup) cmd_backup "$@" ;;
  migrate) cmd_migrate "$@" ;;
  reset) cmd_reset "$@" ;;
  restore) cmd_restore "$@" ;;
  prune) cmd_prune "$@" ;;
  drill) cmd_drill "$@" ;;
  help) usage ;;
  *)
    usage >&2
    exit 2
    ;;
esac
