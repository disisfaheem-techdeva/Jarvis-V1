#!/usr/bin/env bash
# Deploy a branch to this server, with the guards that protect the clinic.
#
#   cd /opt/techdeva-platform/techdeva-platform && bash ops/deploy.sh <branch>
#
# The FIRST time (this file is not on the server yet), run it from the branch:
#   git fetch origin <branch> && git show origin/<branch>:ops/deploy.sh | bash -s -- <branch>
#
# Stops BEFORE changing anything unless: the tree is clean, the branch contains
# everything deployed now (fast-forward only), and a database backup was written.
# Then: fast-forward the code, apply only the NEW numbered migrations (each must
# print "verify ok" if it has a verify block), rebuild only the services whose
# code changed, wait for /health, and compare the numbers that must not move.
# Prints the rollback command at the end, and on any failure after the code moved.
set -euo pipefail
# the repository we are standing in -- works as a file and when piped to bash -s
cd "$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "STOP: run this inside the platform checkout"; exit 1; }
[ -f docker-compose.yml ] && [ -d db ] && [ -d app ] || { echo "STOP: $(pwd) is not the platform checkout"; exit 1; }

BR="${1:?usage: bash ops/deploy.sh <branch>}"
DB="${DB_CONTAINER:-techdeva-platform-db-1}"
API="${API_CONTAINER:-techdeva-platform-api-1}"
# -i only where a file is fed in. `docker exec -i` forwards stdin, and when this
# script itself arrives on stdin (git show ... | bash -s) it would swallow the
# rest of the script; every other docker call gets </dev/null for that reason.
psqlq() { docker exec -i "$DB" psql -v ON_ERROR_STOP=1 -U postgres -d techdeva "$@"; }
psqlc() { docker exec "$DB" psql -v ON_ERROR_STOP=1 -U postgres -d techdeva "$@" </dev/null; }
numbers() {
  psqlc -tA -c "select 'businesses: '||count(*) from tenants where not coalesce(is_test,false)
    union all select 'leads: '||count(*) from leads
    union all select 'users: '||count(*) from users
    union all select 'appointments: '||count(*) from appointments
    union all select 'payments: '||count(*) from client_payments"
}

echo "=== 1. guards (nothing changes until all pass) ==="
[ -z "$(git status --porcelain)" ] || { echo "STOP: uncommitted changes on the server"; git status --short | head; exit 1; }
git fetch -q origin "$BR" </dev/null
OLD=$(git rev-parse HEAD)
NEW=$(git rev-parse "origin/$BR")
[ "$OLD" != "$NEW" ] || { echo "nothing to deploy: already at $(git log -1 --format='%h %s')"; exit 0; }
git merge-base --is-ancestor "$OLD" "$NEW" || { echo "STOP: $BR does not contain what is deployed ($OLD). Rebase it first."; exit 1; }
CHANGED=$(git diff --name-only "$OLD" "$NEW")
MIGRATIONS=$(git diff --name-only --diff-filter=A "$OLD" "$NEW" -- 'db/' | grep -E '^db/[0-9]+_.*\.sql$' | sort -V || true)
SERVICES=""
echo "$CHANGED" | grep -q '^app/' && SERVICES="api worker"
echo "$CHANGED" | grep -q '^techdeva-web/' && SERVICES="$SERVICES web"
echo "$CHANGED" | grep -q '^docker-compose.yml$' && SERVICES="${SERVICES:-api worker}"
echo "deploying $(git log -1 --format='%h %s' "$NEW")"
echo "new migrations: ${MIGRATIONS:-none}"
echo "services to rebuild: ${SERVICES:-none (web/ is bind-mounted and live on pull)}"
df -P / | awk 'NR==2 && $4 < 2000000 { print "STOP: less than 2 GB free on /"; exit 1 }'

echo; echo "=== 2. before ==="
numbers | tee /tmp/td-before.txt

echo; echo "=== 3. backup ==="
B=~/techdeva-backup-$(date +%F-%H%M%S).sql.gz
docker exec "$DB" pg_dump -U postgres techdeva </dev/null | gzip > "$B"
S=$(stat -c%s "$B"); echo "backup: $B ($S bytes)"
[ "$S" -gt "${MIN_BACKUP_BYTES:-100000}" ] || { echo "STOP: the backup looks too small ($S bytes)"; exit 1; }

rollback() {
  echo; echo "!!! FAILED after the code moved. To roll back the code:"
  echo "    git reset --hard $OLD && docker compose build ${SERVICES:-api} && docker compose up -d ${SERVICES:-api}"
  echo "    (migrations are additive; the database backup is $B)"
}
trap rollback ERR

echo; echo "=== 4. code (fast-forward only) ==="
git merge -q --ff-only "$NEW"
git log -1 --format='now at %h %s'

echo; echo "=== 5. migrations ==="
for m in $MIGRATIONS; do
  echo "--- $m"
  out=$(psqlq < "$m" 2>&1) || { echo "$out" | tail -5; exit 1; }
  if grep -q 'verify' "$m"; then
    echo "$out" | grep -E 'verify ok' || { echo "$out" | tail -5; echo "STOP: $m did not say verify ok"; exit 1; }
  fi
done

if [ -n "$SERVICES" ]; then
  echo; echo "=== 6. rebuild: $SERVICES ==="
  docker compose build $SERVICES </dev/null
  docker compose up -d $SERVICES </dev/null
fi

echo; echo "=== 7. verify ==="
for i in $(seq 1 30); do
  docker exec "$API" python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health',timeout=5)" 2>/dev/null && break
  sleep 2
done
docker exec "$API" python -c "import urllib.request;print('health:',urllib.request.urlopen('http://localhost:8000/health',timeout=5).status)"
numbers | tee /tmp/td-after.txt
echo "--- before vs after (expect no difference) ---"
diff /tmp/td-before.txt /tmp/td-after.txt && echo "unchanged"
echo "--- live services ---"
docker ps --format '{{.Names}} {{.Status}}' | grep -Ei 'hermes|cantonment|instantgrill|techdeva-platform' | sort
trap - ERR
echo; echo "=== DONE. Roll back with:  git reset --hard $OLD && docker compose build ${SERVICES:-api} && docker compose up -d ${SERVICES:-api}"
