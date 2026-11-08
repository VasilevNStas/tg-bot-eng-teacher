#!/usr/bin/env bash
# Generate an age key pair for encrypted off-site backups.
#
# The private key stays on the host (or off-site in a password manager); the
# public key goes into BACKUP_AGE_RECIPIENT. Never commit either key.
#
#   ./scripts/age_key.sh
#   ./scripts/age_key.sh --file /secure/age-key.txt
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

KEY_FILE="${AGE_KEY_FILE:-$ROOT_DIR/backups/age-key.txt}"
AGE_KEYGEN_BIN="${AGE_KEYGEN_BIN:-age-keygen}"
FORCE=0

usage() {
  cat <<'EOF'
Usage: ./scripts/age_key.sh [options]

Generates an age key pair (private + public) for backup encryption.

Options:
  --file <path>  Private key path, default: backups/age-key.txt
  --force        Overwrite an existing key (dangerous: old backups become
                 unreadable unless the old key is kept elsewhere)
  -h, --help     Show this help.

Environment:
  AGE_KEY_FILE       private key path
  AGE_KEYGEN_BIN     default: age-keygen

Next steps:
  1. Store the private key outside the VPS (password manager / offline).
  2. Put the public key into BACKUP_AGE_RECIPIENT in .env.
  3. For restore, pass the private key as AGE_IDENTITY.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --file)
      [ "$#" -ge 2 ] || die "--file requires a value"
      KEY_FILE="$2"
      shift
      ;;
    --force) FORCE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done

if [ -e "$KEY_FILE" ] && [ "$FORCE" -ne 1 ]; then
  die "refusing to overwrite $KEY_FILE; pass --force (old backups need the old key)"
fi

command -v "$AGE_KEYGEN_BIN" >/dev/null 2>&1 \
  || die "age-keygen is required but not installed (AGE_KEYGEN_BIN=$AGE_KEYGEN_BIN)"

mkdir -p "$(dirname "$KEY_FILE")"
umask 077
"$AGE_KEYGEN_BIN" -o "$KEY_FILE"
chmod 600 "$KEY_FILE"
public_key="$("$AGE_KEYGEN_BIN" -y "$KEY_FILE")"

printf 'private key: %s\n' "$KEY_FILE"
printf 'public key: %s\n' "$public_key"
printf 'store the private key off the VPS and set BACKUP_AGE_RECIPIENT to the public key\n'
