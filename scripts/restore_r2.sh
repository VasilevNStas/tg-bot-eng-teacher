#!/usr/bin/env bash
# Download an off-site SQLite backup from Cloudflare R2, verify it and drill it.
#
# The script never touches the live database unless `--apply --yes` is passed.
# It downloads the latest object (or `--file`), optionally decrypts `.age`,
# runs `db.sh drill` + `db.sh smoke`, and prints the drill duration as an RTO
# lower bound.
#
#   BACKUP_REMOTE=r2:my-bucket/backups ./scripts/restore_r2.sh --dry-run
#   BACKUP_REMOTE=r2:my-bucket/backups ./scripts/restore_r2.sh
#   BACKUP_REMOTE=r2:my-bucket/backups AGE_IDENTITY=key.txt \
#     ./scripts/restore_r2.sh --file offsite-20261108-030000-1-42.db.age
#   BACKUP_REMOTE=r2:my-bucket/backups ./scripts/restore_r2.sh --apply --yes
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DB_SH="${DB_SH:-$ROOT_DIR/scripts/db.sh}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
RCLONE_BIN="${RCLONE_BIN:-rclone}"
AGE_BIN="${AGE_BIN:-age}"
AGE_IDENTITY="${AGE_IDENTITY:-}"
DRILL_LOG="${DRILL_LOG:-}"

OBJECT=""
DRY_RUN=0
APPLY=0
YES=0
KEEP=0

export DRILL_LOG

usage() {
  cat <<'EOF'
Usage: ./scripts/restore_r2.sh [options]

Downloads an off-site backup, verifies it with `db.sh drill` and `db.sh smoke`.
The live database is replaced only with `--apply --yes`.

Options:
  --file <name>   Download a specific remote object (default: latest).
  --dry-run       Print the selected object and target, do not download.
  --apply         Replace the live database after a successful drill.
  --yes           Required with --apply (destructive).
  --keep          Keep the downloaded/decrypted file and print its path.
  -h, --help      Show this help.

Environment:
  BACKUP_REMOTE    required, e.g. r2:my-bucket/backups
  RCLONE_BIN       default: rclone
  AGE_BIN          default: age
  AGE_IDENTITY     private age key; required for encrypted objects
  DRILL_LOG        optional path for the drill log
  DB_SH            default: scripts/db.sh (override for tests)

Credentials:
  Configure an rclone remote (`rclone config`) or RCLONE_CONFIG_* / AWS_*
  variables. Secrets are never printed by this script.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

info() {
  printf '==> %s\n' "$*" >&2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --file)
      [ "$#" -ge 2 ] || die "--file requires a value"
      OBJECT="$2"
      shift
      ;;
    --dry-run) DRY_RUN=1 ;;
    --apply) APPLY=1 ;;
    --yes) YES=1 ;;
    --keep) KEEP=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done

[ -n "$BACKUP_REMOTE" ] || die "BACKUP_REMOTE is required, e.g. r2:my-bucket/backups"
if [ "$APPLY" -eq 1 ] && [ "$YES" -ne 1 ]; then
  die "refusing to replace the live database without --yes"
fi

remote="${BACKUP_REMOTE%/}"

if [ -z "$OBJECT" ]; then
  command -v "$RCLONE_BIN" >/dev/null 2>&1 \
    || die "rclone is required but not installed (RCLONE_BIN=$RCLONE_BIN)"
  info "list $remote"
  listing="$("$RCLONE_BIN" lsf "$remote" --files-only --format "pt" 2>/dev/null || true)"
  [ -n "$listing" ] || die "no objects found in $remote"
  OBJECT="$(printf '%s\n' "$listing" | sort -t';' -k2 | tail -n 1 | cut -d';' -f1)"
fi
[ -n "$OBJECT" ] || die "could not select a remote object"

remote_path="$remote/$OBJECT"
info "selected $remote_path"

work="$(mktemp -d "${TMPDIR:-/tmp}/restore-r2.XXXXXX")"
cleanup() {
  if [ "$KEEP" -eq 0 ]; then
    rm -rf "$work"
  fi
}
trap cleanup EXIT

downloaded="$work/$(basename "$OBJECT")"

if [ "$DRY_RUN" -eq 1 ]; then
  info "dry-run: would download $remote_path -> $downloaded"
  printf 'remote: %s\n' "$remote_path"
  printf 'local: %s (dry-run)\n' "$downloaded"
  exit 0
fi

command -v "$RCLONE_BIN" >/dev/null 2>&1 \
  || die "rclone is required but not installed (RCLONE_BIN=$RCLONE_BIN)"
info "download $remote_path"
"$RCLONE_BIN" copyto "$remote_path" "$downloaded"

db_file="$downloaded"
case "$downloaded" in
  *.age)
    [ -n "$AGE_IDENTITY" ] || die "AGE_IDENTITY is required to decrypt $downloaded"
    command -v "$AGE_BIN" >/dev/null 2>&1 \
      || die "age is required but not installed (AGE_BIN=$AGE_BIN)"
    db_file="$work/restored.db"
    info "decrypt $downloaded"
    "$AGE_BIN" --decrypt --identity "$AGE_IDENTITY" --output "$db_file" "$downloaded"
    ;;
esac

started="$(date +%s)"
info "drill $db_file"
"$DB_SH" drill "$db_file"
info "smoke $db_file"
"$DB_SH" smoke "$db_file"
duration=$(( $(date +%s) - started ))

printf 'remote: %s\n' "$remote_path"
printf 'database: %s\n' "$db_file"
printf 'rto_seconds: %s\n' "$duration"
if [ -n "$DRILL_LOG" ]; then
  printf 'drill_log: %s\n' "$DRILL_LOG"
fi

if [ "$APPLY" -eq 1 ]; then
  info "apply restore (stops and starts the bot)"
  "$DB_SH" restore "$db_file"
  printf 'applied: %s\n' "$db_file"
else
  printf 'mode: drill only (use --apply --yes to replace the live database)\n'
fi
