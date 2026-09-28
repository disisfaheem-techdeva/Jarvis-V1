#!/bin/bash
# Deploy the techdeva-platform api + worker (+ web when asked) after a database
# backup. Touches ONLY this compose project's api/worker/web services
# (--no-deps): never db or redis, and never any hermes-* container. The worker
# is rebuilt because it runs the same app code (it publishes scheduled posts).
#   bash ops/verify/deploy.sh          # api only
#   bash ops/verify/deploy.sh web      # api + Next.js portal
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -z "$(git status --porcelain)" ] || { echo "ABORT: uncommitted changes. Commit or stash first."; exit 1; }
TS=$(date +%Y%m%d-%H%M%S); BK=/root/backups/part14-$TS; mkdir -p "$BK"
docker compose exec -T db pg_dump -U postgres -Fc techdeva > "$BK/techdeva.dump"
[ -s "$BK/techdeva.dump" ] || { echo "ABORT: backup is empty"; exit 1; }
# The code that was running before this deploy: the commit before the last
# merge/pull if there was one (HEAD@{1}), else HEAD. PREV=<sha> overrides.
PREV="${PREV:-$(git rev-parse HEAD@{1} 2>/dev/null || git rev-parse HEAD)}"
echo "$PREV" > "$BK/code-commit.txt"
echo "backup: $BK ($(du -h "$BK/techdeva.dump" | cut -f1)), code at $(cat "$BK/code-commit.txt")"
for f in $(ls db/[0-9][0-9]_*.sql 2>/dev/null | sort); do
  n=$(basename "$f")
  # a migration newer than 29 is applied once and remembered in a marker table
  case "$n" in 0*|1*|2[0-9]_*) continue;; esac
  applied=$(docker compose exec -T db psql -U postgres -d techdeva -tAc \
    "CREATE TABLE IF NOT EXISTS _td_migrations(name text primary key, applied_at timestamptz default now()); SELECT 1 FROM _td_migrations WHERE name='$n'")
  if [ "$applied" != "1" ]; then
    echo "applying $n"
    docker compose exec -T db psql -U postgres -d techdeva -v ON_ERROR_STOP=1 -q < "$f"
    docker compose exec -T db psql -U postgres -d techdeva -qc "INSERT INTO _td_migrations(name) VALUES ('$n')"
  fi
done
build() {  # show the tail of the build log, and stop if it failed
  if ! docker compose build --progress plain "$1" > "$BK/build-$1.log" 2>&1; then
    tail -40 "$BK/build-$1.log"; echo "ABORT: $1 build failed (full log $BK/build-$1.log). Nothing was restarted."; exit 1
  fi
  echo "$1 built ($(grep -c '' "$BK/build-$1.log") log lines)"
}
build api
build worker
[ "${1:-}" = "web" ] && build web
docker compose up -d --no-deps api worker
[ "${1:-}" = "web" ] && docker compose up -d --no-deps web
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' https://api.techdeva.net/health || true)
  [ "$code" = "200" ] && break; sleep 2
done
echo "api /health: $code"
echo "rollback code: git reset --hard $(cat "$BK/code-commit.txt") && docker compose build api worker web && docker compose up -d --no-deps api worker web"
echo "rollback data: docker compose exec -T db pg_restore -U postgres -d techdeva --clean < $BK/techdeva.dump   (only if a migration went wrong)"
