#!/bin/bash
# Deploy the techdeva-platform api (+ web when asked) after a database backup.
# Touches ONLY this compose project's api/web services (--no-deps): never db,
# redis, worker, and never any hermes-* container.
#   bash ops/verify/deploy.sh          # api only
#   bash ops/verify/deploy.sh web      # api + Next.js portal
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -z "$(git status --porcelain)" ] || { echo "ABORT: uncommitted changes. Commit or stash first."; exit 1; }
TS=$(date +%Y%m%d-%H%M%S); BK=/root/backups/part14-$TS; mkdir -p "$BK"
docker compose exec -T db pg_dump -U postgres -Fc techdeva > "$BK/techdeva.dump"
[ -s "$BK/techdeva.dump" ] || { echo "ABORT: backup is empty"; exit 1; }
git rev-parse HEAD > "$BK/code-commit.txt"
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
docker compose build api >/dev/null && docker compose up -d --no-deps api
if [ "${1:-}" = "web" ]; then docker compose build web >/dev/null && docker compose up -d --no-deps web; fi
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' https://api.techdeva.net/health || true)
  [ "$code" = "200" ] && break; sleep 2
done
echo "api /health: $code"
echo "rollback code: git checkout $(cat "$BK/code-commit.txt") -- . && docker compose build api && docker compose up -d --no-deps api"
echo "rollback data: docker compose exec -T db pg_restore -U postgres -d techdeva --clean < $BK/techdeva.dump   (only if a migration went wrong)"
