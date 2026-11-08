#!/usr/bin/env bash
# Verify (and optionally create) the rclone remote used for off-site backups.
#
# This script never prints credentials. It checks that the remote is reachable
# and performs a write/list/delete probe so a broken bucket or token is caught
# before the first nightly backup.
#
#   BACKUP_REMOTE=r2:my-bucket/backups ./scripts/setup_r2.sh --dry-run
#   BACKUP_REMOTE=r2:my-bucket/backups ./scripts/setup_r2.sh --init
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BACKUP_REMOTE="${BACKUP_REMOTE:-}"
RCLONE_BIN="${RCLONE_BIN:-rclone}"

DRY_RUN=0
INIT=0

usage() {
  cat <<'EOF'
Usage: ./scripts/setup_r2.sh [options]

Checks the configured rclone remote and performs a write/list/delete probe.

Options:
  --init      Create the remote prefix with `rclone mkdir` first.
  --dry-run   Print the commands without touching the remote.
  -h, --help  Show this help.

Environment:
  BACKUP_REMOTE   required, e.g. r2:my-bucket/backups
  RCLONE_BIN      default: rclone

Credentials:
  Configure `rclone config` or RCLONE_CONFIG_* / AWS_* variables first.
  Secrets are never printed by this script.
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
    --init) INIT=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done

[ -n "$BACKUP_REMOTE" ] || die "BACKUP_REMOTE is required, e.g. r2:my-bucket/backups"
remote="${BACKUP_REMOTE%/}"
probe=".probe-$(date +%Y%m%d-%H%M%S)-$$"

if [ "$DRY_RUN" -eq 1 ]; then
  info "dry-run: would check $remote"
  printf 'rclone about %s\n' "$remote"
  if [ "$INIT" -eq 1 ]; then
    printf 'rclone mkdir %s\n' "$remote"
  fi
  printf 'rclone copyto <probe> %s/%s\n' "$remote" "$probe"
  printf 'rclone lsf %s/%s\n' "$remote" "$probe"
  printf 'rclone deletefile %s/%s\n' "$remote" "$probe"
  exit 0
fi

command -v "$RCLONE_BIN" >/dev/null 2>&1 \
  || die "rclone is required but not installed (RCLONE_BIN=$RCLONE_BIN)"

tmp="$(mktemp "${TMPDIR:-/tmp}/r2-probe.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
printf 'probe\n' > "$tmp"

info "check remote $remote"
if ! "$RCLONE_BIN" about "$remote" >/dev/null 2>&1; then
  info "about not supported by this remote; continuing"
fi

if [ "$INIT" -eq 1 ]; then
  info "create remote prefix"
  "$RCLONE_BIN" mkdir "$remote"
fi

info "write probe"
"$RCLONE_BIN" copyto "$tmp" "$remote/$probe"

info "list probe"
"$RCLONE_BIN" lsf "$remote/$probe" >/dev/null || die "probe is not visible in $remote"

info "delete probe"
"$RCLONE_BIN" deletefile "$remote/$probe"

printf 'remote: %s\n' "$remote"
printf 'setup: ok\n'
