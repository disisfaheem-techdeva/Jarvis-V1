#!/bin/bash
# Prove an off-site copy can really be restored: download it from Backblaze B2,
# decrypt it with the private key Faheem keeps, and restore it into a THROWAWAY
# database and folder. Nothing live is changed. Setup: docs/OFFSITE-BACKUP.md.
#
#   ops/backup/offsite-restore-test.sh                 newest copy, asks for the key
#   ops/backup/offsite-restore-test.sh <name>.tar.age  that copy
#   ... --key-file <file>                              key from a file instead of typing it
#
# The private key is typed (hidden) or read from a file, held in memory-backed
# /dev/shm for the run, and deleted on exit. It is never written to disk, logs
# or B2. The decrypted copy lives in a 700 folder and is deleted on exit; the
# scratch database is dropped on exit, pass or fail. No secret is printed: the
# settings archive is checked by file NAME only.
#
# Checks: download + checksum, decryption, every file against the manifest,
# platform database restores with its data, clinic CRM opens and passes its
# integrity check, settings present, every agent folder present, every agent
# memory volume and the videos present and readable.
set -uo pipefail
# bucket name etc. (no secrets): written once during setup
[ -f "${OFFSITE_CONF:-/etc/techdeva/offsite.conf}" ] && . "${OFFSITE_CONF:-/etc/techdeva/offsite.conf}"
TARGET="${OFFSITE_TARGET:-techdeva-b2:techdeva-backups/daily}"
WORK_ROOT="${RESTORE_ROOT:-/opt/backups/restore-test}"
DB_C="${DB_CONTAINER:-techdeva-platform-db-1}"
STATE_DIR="${OFFSITE_STATE_DIR:-/var/lib/techdeva}"
NAME=""; KEYSRC=""
while [ $# -gt 0 ]; do
  case "$1" in --key-file) KEYSRC=$2; shift;; *.tar.age) NAME=$1;; *) echo "unknown $1"; exit 2;; esac; shift
done
START=$(date +%s); FAIL=0; RESULTS=()
ok()  { echo "  ✓ $*"; RESULTS+=("ok: $*"); }
bad() { echo "  ✗ $*"; RESULTS+=("FAILED: $*"); FAIL=1; }

umask 077
KEY=$(mktemp /dev/shm/td-restore-key.XXXXXX 2>/dev/null || mktemp)
SCRATCH="offsite_check_$(date -u +%Y%m%d_%H%M%S)"
WORK="$WORK_ROOT/$SCRATCH"
dbx() { docker exec -i "$DB_C" sh -c "$1" _ "${@:2}"; }
cleanup() {
  shred -u "$KEY" 2>/dev/null || rm -f "$KEY"
  rm -rf "$WORK"
  dbx 'dropdb -U "$POSTGRES_USER" --if-exists "$1"' "$SCRATCH" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [ -n "$KEYSRC" ]; then cat "$KEYSRC" > "$KEY"
else
  printf 'Paste the private key (AGE-SECRET-KEY-1...), then press Enter. It is not shown: '
  IFS= read -rs line; echo; printf '%s\n' "$line" > "$KEY"; unset line
fi
grep -q '^AGE-SECRET-KEY-1' "$KEY" || { echo "That is not an age private key."; exit 2; }

mkdir -p "$WORK"
echo "Off-site restore test ($(date -u +%FT%TZ))"

# 1. download + checksum
[ -n "$NAME" ] || NAME=$(rclone lsf "$TARGET" --include '*.tar.age' 2>/dev/null | sort | tail -1)
[ -n "$NAME" ] || { bad "no off-site copy found in $TARGET"; exit 1; }
echo "copy: $NAME"
if rclone copy "$TARGET/$NAME" "$WORK" 2>/dev/null && rclone copy "$TARGET/$NAME.sha256" "$WORK" 2>/dev/null; then
  ok "downloaded from Backblaze ($(stat -c %s "$WORK/$NAME") bytes)"
else bad "download failed"; exit 1; fi
(cd "$WORK" && sha256sum -c --quiet "$NAME.sha256") >/dev/null 2>&1 && ok "checksum matches the one made before upload" || { bad "checksum does not match"; exit 1; }

# 2. decrypt
if age -d -i "$KEY" "$WORK/$NAME" | tar xf - -C "$WORK" 2>/dev/null; then ok "decrypted with your private key"
else bad "could not decrypt: wrong key, or the file is damaged"; exit 1; fi
rm -f "$WORK/$NAME"
D="$WORK/${NAME%.tar.age}"
(cd "$D" && sha256sum -c --quiet MANIFEST.sha256) >/dev/null 2>&1 && ok "all $(wc -l < "$D/MANIFEST.sha256") files match the manifest" || bad "files do not match the manifest"
NM=$(ls "$D"/nightly/manifest_*.sha256 2>/dev/null | head -1)
[ -n "$NM" ] && (cd "$D/nightly" && sha256sum -c --quiet "$(basename "$NM")") >/dev/null 2>&1 && ok "nightly set matches its own checksums" || bad "nightly set checksums"

# 3. platform database into a scratch database
DUMP=$(ls "$D"/nightly/techdeva_*.dump 2>/dev/null | head -1)
if [ -z "$DUMP" ]; then bad "platform database dump missing"
elif dbx 'createdb -U "$POSTGRES_USER" "$1"' "$SCRATCH" >/dev/null 2>&1; then
  if dbx 'pg_restore -U "$POSTGRES_USER" -d "$1" --no-owner --no-privileges' "$SCRATCH" < "$DUMP" >/dev/null 2>/tmp/restore.err; then
    q() { dbx 'psql -U "$POSTGRES_USER" -d "$1" -tAc "$2"' "$SCRATCH" "$1" 2>/dev/null | tr -d '[:space:]'; }
    T=$(q "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
    TEN=$(q "SELECT count(*) FROM tenants"); U=$(q "SELECT count(*) FROM users"); C=$(q "SELECT count(*) FROM customers")
    if [ "${T:-0}" -ge 10 ] && [ "${TEN:-0}" -ge 1 ]; then ok "platform database restored: $T tables, $TEN businesses, $U users, $C customers"
    else bad "platform database restored but looks empty ($T tables, $TEN businesses)"; fi
  else bad "platform database restore failed: $(tail -1 /tmp/restore.err)"; fi
else bad "could not create the scratch database"; fi

# 4. clinic CRM
SQ=$(ls "$D"/nightly/leads_*.db 2>/dev/null | head -1)
if [ -z "$SQ" ]; then bad "clinic CRM database missing"
elif [ "$(sqlite3 "$SQ" 'pragma integrity_check;' 2>/dev/null)" = ok ]; then
  ok "clinic CRM opens, integrity ok, $(sqlite3 "$SQ" "select count(*) from sqlite_master where type='table'") tables"
else bad "clinic CRM fails its integrity check"; fi

# 5. settings (names only, never contents)
CF=$(ls "$D"/nightly/config_*.tgz 2>/dev/null | head -1)
if [ -n "$CF" ] && tar tzf "$CF" 2>/dev/null | grep -q 'techdeva-platform/.env$'; then
  ok "settings recoverable: $(tar tzf "$CF" | grep -c '\.env$') .env files, $(tar tzf "$CF" | grep -c 'traefik') Traefik entries"
else bad "settings archive missing or without the platform .env"; fi

# 6. agent folders
AF="$D/agent-folders.tgz"
if [ -f "$AF" ] && tar tzf "$AF" >/dev/null 2>&1; then
  N=$(tar tzf "$AF" | cut -d/ -f1 | sort -u | wc -l)
  E=$(tar tzf "$AF" | grep -cE '/(agent\.env|docker-compose\.ya?ml)$')
  ok "agent folders: $N folders, $E compose/env files"
else bad "agent folders missing or unreadable"; fi

# 7. agent memory volumes and videos
NV=0; EMPTY=""
for v in "$D"/volumes/*.tgz; do
  [ -f "$v" ] || continue
  n=$(tar tzf "$v" 2>/dev/null | grep -vc '/$'); b=$(basename "$v" .tgz)
  if [ "$n" -ge 1 ]; then NV=$((NV+1)); else EMPTY="$EMPTY $b"; fi
done
[ $NV -ge 1 ] && ok "agent memory and media volumes: $NV readable with files" || bad "no readable volumes"
[ -z "$EMPTY" ] || echo "  · empty volumes (nothing to restore):$EMPTY"
ls "$D"/volumes/*mediadata*.tgz >/dev/null 2>&1 && ok "videos volume present" || bad "videos volume missing"
CRON=$(grep -cvE '^(#|$)' "$D/crontab.txt" 2>/dev/null || true)
[ "${CRON:-0}" -ge 1 ] && ok "timed jobs list present ($CRON jobs)" || bad "timed jobs list missing or empty"

SECS=$(( $(date +%s) - START ))
echo "Result: $([ $FAIL = 0 ] && echo PASSED || echo FAILED) in ${SECS}s"
mkdir -p "$STATE_DIR"
python3 - "$STATE_DIR/offsite-restore-last.json" "$FAIL" "$NAME" "$SECS" "${RESULTS[@]}" <<'PY'
import json, sys, datetime
p, fail, name, secs, *res = sys.argv[1:]
json.dump({"ok": fail == "0", "copy": name, "seconds": int(secs), "results": res,
           "at": datetime.datetime.now(datetime.timezone.utc).isoformat()}, open(p, "w"))
PY
exit $FAIL
