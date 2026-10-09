#!/bin/bash
# Off-server copy of the nightly backup: encrypted on this server, then sent to
# Backblaze B2. Setup and restore: docs/OFFSITE-BACKUP.md.
#
# The nightly backup (/opt/techdeva-backup.sh, 02:15) writes everything to
# /opt/backups/daily ON THIS SERVER, so losing the server loses every backup.
# This runs after it (cron) and sends ONE encrypted file off the server:
#
#   nightly/          the newest nightly set exactly as made: platform database,
#                     clinic CRM database, settings (.env files, Traefik config)
#   volumes/          every hermes-* docker volume (each WhatsApp agent's memory,
#                     the clinic's Zara and workspace included) and the videos.
#                     Read-only: a throwaway alpine container mounts the volume
#                     :ro and tars it, capped at half a CPU. No agent container is
#                     stopped, restarted or touched.
#   agent-folders.tgz /docker/hermes-* (each agent's compose file, agent.env, SOUL)
#   crontab.txt       root's crontab, to rebuild the timed jobs
#   MANIFEST.sha256   checksum of every file above
#
# Encryption is age with a PUBLIC key (/etc/techdeva/backup-recipient.txt). The
# server can lock backups but never unlock them: the private key is kept by
# Faheem outside the server. It is never in .env, never in B2, never in GitHub.
# The script refuses to run if a private key is found where the public key goes,
# and refuses to upload anything that is not an age-encrypted file.
#
# Upload only after the nightly backup itself succeeded. Any failure (nightly
# not run or failed, a copy failing, encryption, upload, or the uploaded file's
# checksum) makes this exit non-zero and is written to
# /var/lib/techdeva/offsite-last.json for the monitor.
# Retention (30 days) is a Backblaze lifecycle rule on the bucket: the B2 key on
# this server cannot delete, so a compromised server cannot wipe the copies.
#
#   ops/backup/offsite.sh              make and upload tonight's copy
#   ops/backup/offsite.sh --dry-run    show what it would copy, upload nothing
set -uo pipefail
# bucket name etc. (no secrets): written once during setup
[ -f "${OFFSITE_CONF:-/etc/techdeva/offsite.conf}" ] && . "${OFFSITE_CONF:-/etc/techdeva/offsite.conf}"
TARGET="${OFFSITE_TARGET:-techdeva-b2:techdeva-backups/daily}"
RECIPIENT="${OFFSITE_RECIPIENT:-/etc/techdeva/backup-recipient.txt}"
DAILY="${BACKUP_DIR:-/opt/backups/daily}"
NIGHTLY_LOG="${NIGHTLY_LOG:-/var/log/techdeva-backup.log}"
STAGE_ROOT="${OFFSITE_STAGE:-/opt/backups/offsite-stage}"
MAX_MB="${OFFSITE_MAX_ITEM_MB:-5000}"
BWLIMIT="${OFFSITE_BWLIMIT:-4M}"          # leave bandwidth for the clinic
STATE_DIR="${OFFSITE_STATE_DIR:-/var/lib/techdeva}"
DOCKER_ROOT="${OFFSITE_DOCKER_ROOT:-/docker}"
MEDIA_VOL="${OFFSITE_MEDIA_VOLUME:-techdeva-platform_mediadata}"
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
RUN="$(date -u +%Y%m%d_%H%M%SZ)"
START=$(date +%s)
FAIL=0; NOTES=(); BYTES=0
say() { echo "[$(date -uIs)] $*"; }
bad() { say "ERROR: $*"; NOTES+=("$*"); FAIL=1; }
status() {
  mkdir -p "$STATE_DIR"
  python3 - "$STATE_DIR/offsite-last.json" "$([ $FAIL = 0 ] && echo true || echo false)" "$RUN" "$BYTES" \
            "$(( $(date +%s) - START ))" "${NOTES[*]:-}" <<'PY'
import json, sys
p, ok, run, b, secs, notes = sys.argv[1:]
json.dump({"ok": ok == "true", "run": run, "bytes": int(b or 0), "seconds": int(secs),
           "problems": notes}, open(p, "w"))
PY
}
stop() { status; say "done. failures=$FAIL"; exit "${1:-1}"; }

mkdir -p "$STATE_DIR"
exec 9>"${OFFSITE_LOCK:-/var/lock/techdeva-offsite.lock}"
flock -n 9 || { say "another off-site run is in progress"; exit 0; }

# 1. tools and the public key
for t in age rclone docker python3; do command -v $t >/dev/null || bad "$t is not installed"; done
[ $FAIL = 0 ] || stop 2
[ -f "$RECIPIENT" ] || { bad "no public key at $RECIPIENT (docs/OFFSITE-BACKUP.md step 2)"; stop 2; }
if grep -q "AGE-SECRET-KEY" "$RECIPIENT"; then
  bad "$RECIPIENT holds a PRIVATE key. Remove it from the server; only the public key (age1...) belongs there"; stop 2
fi
grep -qE '^age1[0-9a-z]{50,}$' "$RECIPIENT" || { bad "$RECIPIENT has no age public key (age1...)"; stop 2; }
REMOTE="${TARGET%%:*}"
rclone listremotes 2>/dev/null | grep -qx "$REMOTE:" || { bad "rclone remote $REMOTE: is not set up (docs/OFFSITE-BACKUP.md step 3)"; stop 2; }

# 2. only after the nightly backup succeeded
MAN=$(ls -1t "$DAILY"/manifest_*.sha256 2>/dev/null | head -1)
[ -n "$MAN" ] || { bad "no nightly backup in $DAILY"; stop 1; }
NSTAMP=$(basename "$MAN" .sha256); NSTAMP=${NSTAMP#manifest_}
AGE_H=$(( ($(date +%s) - $(stat -c %Y "$MAN")) / 3600 ))
[ "$AGE_H" -le 30 ] || bad "newest nightly backup is ${AGE_H}h old: the nightly job is not running"
LAST=$(grep -E '\] done\. failures=' "$NIGHTLY_LOG" 2>/dev/null | tail -1)
case "$LAST" in *"failures=0"*) ;; *) bad "the nightly backup did not succeed (last result: ${LAST:-none})";; esac
(cd "$DAILY" && sha256sum -c --quiet "$(basename "$MAN")") >/dev/null 2>&1 || bad "nightly set $NSTAMP fails its own checksum"
[ $FAIL = 0 ] || { say "not uploading: the local backup must succeed first"; stop 1; }

# 3. what goes off-site tonight
VOLS=$(docker volume ls --format '{{.Name}}' 2>/dev/null | grep -E '^hermes-' || true)
docker volume inspect "$MEDIA_VOL" >/dev/null 2>&1 && VOLS="$VOLS
$MEDIA_VOL"
VOLS=$(printf '%s\n' "$VOLS" | sed '/^$/d')
FOLDERS=$(cd "$DOCKER_ROOT" 2>/dev/null && ls -d hermes-* 2>/dev/null || true)
if [ $DRY = 1 ]; then
  say "dry run: nightly set $NSTAMP: $(awk '{print $2}' "$MAN" | tr '\n' ' ')"
  for v in $VOLS; do
    mb=$(docker run --rm --network none --cpus 0.5 --memory 256m -v "$v":/v:ro alpine du -sm /v 2>/dev/null | cut -f1)
    say "dry run: volume $v, ${mb:-?} MB (read-only)"
  done
  say "dry run: agent folders: $(echo $FOLDERS)"
  say "dry run: would upload $RUN.tar.age to $TARGET"
  exit 0
fi

umask 077
STAGE="$STAGE_ROOT/$RUN"; OUT="$STAGE_ROOT/$RUN.tar.age"
mkdir -p "$STAGE/nightly" "$STAGE/volumes"
trap 'rm -rf "$STAGE" "$OUT" "$OUT.sha256"' EXIT

cp -p "$MAN" "$STAGE/nightly/" || bad "could not stage $MAN"
for f in $(awk '{print $2}' "$MAN"); do cp -p "$DAILY/$f" "$STAGE/nightly/" || bad "could not stage $f"; done

for v in $VOLS; do
  mb=$(docker run --rm --network none --cpus 0.5 --memory 256m -v "$v":/v:ro alpine du -sm /v 2>/dev/null | cut -f1)
  if [ -z "$mb" ]; then bad "volume $v: could not read it"; continue; fi
  if [ "$mb" -gt "$MAX_MB" ]; then bad "volume $v is ${mb} MB, over the ${MAX_MB} MB limit: not copied"; continue; fi
  if docker run --rm --network none --cpus 0.5 --memory 256m -v "$v":/v:ro alpine tar czf - -C /v . > "$STAGE/volumes/$v.tgz" 2>/dev/null \
     && gzip -t "$STAGE/volumes/$v.tgz" 2>/dev/null; then
    say "volume $v: ${mb} MB read, $(stat -c %s "$STAGE/volumes/$v.tgz") bytes packed"
  else bad "volume $v: copy failed"; rm -f "$STAGE/volumes/$v.tgz"; fi
done

if [ -n "$FOLDERS" ]; then
  mb=$(cd "$DOCKER_ROOT" && du -smc $FOLDERS | tail -1 | cut -f1)
  if [ "$mb" -gt "$MAX_MB" ]; then bad "agent folders are ${mb} MB, over the limit: not copied"
  elif (cd "$DOCKER_ROOT" && tar czf "$STAGE/agent-folders.tgz" $FOLDERS 2>/dev/null); then
    say "agent folders: $(echo $FOLDERS | wc -w) folders, $(stat -c %s "$STAGE/agent-folders.tgz") bytes"
  else bad "agent folders: copy failed"; fi
else bad "no agent folders in $DOCKER_ROOT"; fi

crontab -l > "$STAGE/crontab.txt" 2>/dev/null || true
(cd "$STAGE" && find . -type f ! -name MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum > MANIFEST.sha256)

# 4. encrypt: one file, readable only with the private key Faheem keeps
if tar cf - -C "$STAGE_ROOT" "$RUN" | age -R "$RECIPIENT" -o "$OUT" 2>/tmp/offsite.err \
   && [ "$(head -c 21 "$OUT")" = "age-encryption.org/v1" ]; then
  BYTES=$(stat -c %s "$OUT"); say "encrypted: $BYTES bytes"
else bad "encryption failed: $(tail -1 /tmp/offsite.err 2>/dev/null)"; stop 1; fi
rm -rf "$STAGE"                                   # no plaintext copy left behind
(cd "$STAGE_ROOT" && sha256sum "$RUN.tar.age" > "$RUN.tar.age.sha256")

# 5. upload, then prove what B2 holds is byte-for-byte what was encrypted
if rclone copy "$OUT" "$TARGET" --bwlimit "$BWLIMIT" --retries 5 --low-level-retries 10 2>/tmp/offsite.err \
   && rclone copy "$OUT.sha256" "$TARGET" --retries 5 2>>/tmp/offsite.err; then
  if rclone check "$STAGE_ROOT" "$TARGET" --one-way --include "$RUN.tar.age*" 2>/tmp/offsite.err; then
    say "uploaded and checked: $RUN.tar.age ($BYTES bytes) -> $TARGET"
  else bad "the uploaded copy does not match: $(tail -1 /tmp/offsite.err)"; fi
else bad "upload failed: $(tail -1 /tmp/offsite.err)"; fi

stop $FAIL
