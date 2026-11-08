#!/usr/bin/env bash
# Off-site SQLite backup to an S3-compatible bucket (Cloudflare R2).
#
# The script creates a consistent local backup with `scripts/db.sh backup`,
# verifies it, optionally encrypts it with `age`, and uploads it with `rclone`.
# Credentials live in the rclone config or rclone environment variables; this
# script never reads or prints them.
#
#   ./scripts/backup_r2.sh --dry-run
#   BACKUP_REMOTE=r2:my-bucket/backups ./scripts/backup_r2.sh
#   BACKUP_REMOTE=r2:my-bucket/backups \
#     BACKUP_AGE_RECIPIENT=age1... ./scripts/backup_r2.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DB_PATH="${DB_PATH:-$ROOT_DIR/data/bot.db}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
BACKUP_RETENTION="${BACKUP_RETENTION:-10}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
RCLONE_BIN="${RCLONE_BIN:-rclone}"
AGE_BIN="${AGE_BIN:-age}"
BACKUP_AGE_RECIPIENT="${BACKUP_AGE_RECIPIENT:-}"
AGE_IDENTITY="${AGE_IDENTITY:-}"
REMOTE_RETENTION_DAYS="${REMOTE_RETENTION_DAYS:-90}"
BACKUP_PING_URL="${BACKUP_PING_URL:-}"
PING_BIN="${PING_BIN:-curl}"

export DB_PATH BACKUP_DIR BACKUP_RETENTION

DRY_RUN=0
NO_ENCRYPT=0
PRUNE_REMOTE=0
VERIFY_DOWNLOAD=0
BACKUP_OK=0
VERIFY_DIR=""

usage() {
  cat <<'EOF'
Usage: ./scripts/backup_r2.sh [options]

Creates a local SQLite backup, verifies it, optionally encrypts it and uploads
it to an S3-compatible bucket.

Options:
  --dry-run        Create and verify the backup, print upload target, do not upload.
  --no-encrypt     Upload the plain .db even if BACKUP_AGE_RECIPIENT is set.
  --prune-remote   Delete remote objects older than REMOTE_RETENTION_DAYS.
  --verify-download  Download the uploaded object back and check integrity.
  -h, --help       Show this help.

Environment:
  DB_PATH                   default: ./data/bot.db
  BACKUP_DIR                default: ./backups
  BACKUP_RETENTION          local files to keep, default: 10
  BACKUP_REMOTE             required, e.g. r2:my-bucket/backups
  RCLONE_BIN                default: rclone
  BACKUP_AGE_RECIPIENT      optional age public key; encrypts before upload
  AGE_IDENTITY              private age key for --verify-download of .age
  AGE_BIN                   default: age
  REMOTE_RETENTION_DAYS     used by --prune-remote, default: 90
  BACKUP_PING_URL           optional dead-man's switch; pinged on success,
                            `.../fail` is pinged on any failure
  PING_BIN                  default: curl

Credentials:
  Configure an rclone remote (for example `rclone config`) or export the
  RCLONE_CONFIG_* / AWS_* variables. Keep secrets out of git and logs.

Note:
  The R2 path is not exercised by the test suite without credentials. See
  .opencode/_db_operations.md for what is verified and what remains tech debt.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '==> %s\n' "$*" >&2
}

ping_backup() {
  local url="$1"
  [ -n "$url" ] || return 0
  if command -v "$PING_BIN" >/dev/null 2>&1; then
    "$PING_BIN" -fsS --max-time 10 "$url" >/dev/null 2>&1 || info "ping failed"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- --timeout=10 "$url" >/dev/null 2>&1 || info "ping failed"
  else
    info "ping skipped: curl/wget not found"
  fi
  return 0
}

on_exit() {
  if [ -n "$VERIFY_DIR" ]; then
    rm -rf "$VERIFY_DIR"
  fi
  if [ "$BACKUP_OK" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
    ping_backup "${BACKUP_PING_URL%/}/fail"
  fi
}
trap on_exit EXIT

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --no-encrypt) NO_ENCRYPT=1 ;;
    --prune-remote) PRUNE_REMOTE=1 ;;
    --verify-download) VERIFY_DOWNLOAD=1 ;;
    -h|--help) BACKUP_OK=1; usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done

[ -n "$BACKUP_REMOTE" ] || die "BACKUP_REMOTE is required, e.g. r2:my-bucket/backups"
[ -f "$DB_PATH" ] || die "database not found: $DB_PATH"
command -v sqlite3 >/dev/null 2>&1 || die "sqlite3 is required"

info "create local backup"
backup_path="$("$ROOT_DIR/scripts/db.sh" backup offsite)"
[ -f "$backup_path" ] || die "backup was not created: $backup_path"

info "verify $backup_path"
result="$(sqlite3 "$backup_path" 'PRAGMA integrity_check;' 2>&1 || true)"
[ "$result" = "ok" ] || die "integrity check failed: $result"

upload_file="$backup_path"
if [ -n "$BACKUP_AGE_RECIPIENT" ] && [ "$NO_ENCRYPT" -eq 0 ]; then
  encrypted="${backup_path}.age"
  if command -v "$AGE_BIN" >/dev/null 2>&1; then
    info "encrypt with age"
    "$AGE_BIN" --encrypt --recipient "$BACKUP_AGE_RECIPIENT" --output "$encrypted" "$backup_path"
    upload_file="$encrypted"
  elif [ "$DRY_RUN" -eq 1 ]; then
    info "dry-run: age not found, would encrypt with the configured recipient"
    upload_file="$encrypted"
  else
    die "age is required for encryption but not installed (or pass --no-encrypt)"
  fi
fi

remote_target="${BACKUP_REMOTE%/}/$(basename "$upload_file")"

if [ "$DRY_RUN" -eq 1 ]; then
  info "dry-run: would upload $upload_file -> $remote_target"
else
  command -v "$RCLONE_BIN" >/dev/null 2>&1 \
    || die "rclone is required for upload but not installed (RCLONE_BIN=$RCLONE_BIN)"
  info "upload $upload_file -> $remote_target"
  "$RCLONE_BIN" copyto "$upload_file" "$remote_target"
  "$RCLONE_BIN" lsf "$remote_target" >/dev/null \
    || die "upload verification failed: $remote_target"
fi

if [ "$VERIFY_DOWNLOAD" -eq 1 ]; then
  if [ "$DRY_RUN" -eq 1 ]; then
    info "dry-run: would download and verify $remote_target"
  else
    VERIFY_DIR="$(mktemp -d "${TMPDIR:-/tmp}/backup-verify.XXXXXX")"
    verify_file="$VERIFY_DIR/$(basename "$upload_file")"
    info "download for verification"
    "$RCLONE_BIN" copyto "$remote_target" "$verify_file"
    verify_db="$verify_file"
    case "$verify_file" in
      *.age)
        [ -n "$AGE_IDENTITY" ] || die "AGE_IDENTITY is required to verify encrypted download"
        command -v "$AGE_BIN" >/dev/null 2>&1 \
          || die "age is required to verify encrypted download (AGE_BIN=$AGE_BIN)"
        verify_db="$VERIFY_DIR/verify.db"
        "$AGE_BIN" --decrypt --identity "$AGE_IDENTITY" --output "$verify_db" "$verify_file"
        ;;
    esac
    result="$(sqlite3 "$verify_db" 'PRAGMA integrity_check;' 2>&1 || true)"
    [ "$result" = "ok" ] || die "downloaded backup integrity check failed: $result"
    info "verify: ok"
  fi
fi

if [ "$PRUNE_REMOTE" -eq 1 ]; then
  if [ "$DRY_RUN" -eq 1 ]; then
    info "dry-run: would delete remote objects older than ${REMOTE_RETENTION_DAYS}d"
  else
    command -v "$RCLONE_BIN" >/dev/null 2>&1 || die "rclone is required for --prune-remote"
    info "delete remote objects older than ${REMOTE_RETENTION_DAYS}d"
    "$RCLONE_BIN" delete "$BACKUP_REMOTE" --min-age "${REMOTE_RETENTION_DAYS}d"
  fi
fi

BACKUP_OK=1
if [ "$DRY_RUN" -eq 0 ]; then
  ping_backup "$BACKUP_PING_URL"
fi

printf 'local backup: %s\n' "$backup_path"
if [ "$DRY_RUN" -eq 1 ]; then
  printf 'remote target: %s (dry-run)\n' "$remote_target"
else
  printf 'remote target: %s (uploaded)\n' "$remote_target"
fi
