# Deploy: email verification by 6-digit code

Built and tested in `/root/techdeva-email-verify` (a local clone, no remote). Nothing
here has been deployed yet. Every step below is run **by hand, one at a time**, and
you stop if any check fails.

```
L=/opt/techdeva-platform/techdeva-platform      # live
W=/root/techdeva-email-verify                   # this working copy
cd $L
```

## Rules for every command

- **InstantGrill is in this compose project** (`instantgrill-new`) and uses **the same
  db container** (database `instantgrill`). So every compose command names one
  TechDeva service and uses `--no-deps`, for example
  `docker compose up -d --no-deps --build api`.
  **Never** run a bare `docker compose up -d`, `down`, `restart`, `stop` or `pull`, or
  any compose command without a service name. No `docker system/image/volume prune`.
- `psql` and `pg_dump` only with `-d techdeva`. Never `instantgrill`.
- Never `cat`, `grep` or print `.env`. Edit it with `nano` only.
- Do not touch `worker`, `db`, `redis`, `voice`, `instantgrill-new` or anything outside `$L`.

## What changes

| File | What |
|---|---|
| `db/79_email_codes.sql` | new: `email_codes` table, `email_verification_backfill` table, marks **every existing user verified** |
| `app/email_codes.py` | new: code issue/check (HMAC, 10 min, 5 tries, 1/min + 5/hour), `POST /auth/verify-email`, `POST /auth/resend-code` |
| `app/main.py` | `/signup` sends a code; response adds `verification_required`, `email_sent` (and `email_message` when it did not go). Router registered |
| `app/portal.py` | `/auth/login`: right password + unverified → `403 {"detail":"email not verified","verification_required":true}`, no session, fresh code sent. `/auth/set-password` marks the email verified |
| `techdeva-web/src/app/login/page.tsx` | portal.techdeva.net/login: the code step, resend with 60 s countdown, then signs in |
| `web/signup.html` | the code step after signup, before the "trial started" screen. **Live the moment it is copied** |
| `web/dashboard.html`, `web/portal.html` | their own sign-in boxes show the same code step when login answers `verification_required`, then sign in. **Live the moment they are copied** |
| `tests/test_email_codes.py` | 21 tests (scratch DB only; the file refuses to run against `@db:`) |

Not changed: `app/account.py` (`send_mail` is reused as it is), `app/security.py`
(old 24 h links via `GET /auth/verify-email` keep working), `worker`, compose files.

Plain text only: `account.send_mail` sends plain text, so the code email has no HTML part.

## 0. Pre-checks (read-only)

```bash
cd $L
git status --short            # must be empty
git log --oneline -1          # must be b3d3956 (the clone was made from this)
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'techdeva-platform|instantgrill' > /root/pre-deploy-uptimes.txt
cat /root/pre-deploy-uptimes.txt
```

If `git status` is not clean or HEAD moved: **stop**. Someone changed live. Re-check the diff.

## 1. Mail settings in `.env` (you, by hand; never give the password to Claude)

The live api uses **Gmail** right now (`smtp.gmail.com:587`, `disisfaheem@gmail.com`).
To send from `admin@techdeva.net` through Hostinger, change these five lines in
`$L/.env` with `nano $L/.env` (names only here; you type the values):

```
SMTP_HOST=smtp.hostinger.com
SMTP_PORT=465
SMTP_USER=admin@techdeva.net
SMTP_PASSWORD=<the mailbox password, typed by you>
MAIL_FROM=TechDeva <admin@techdeva.net>
```

Port 465 = implicit SSL; `account.send_mail` already does `SMTP_SSL` for 465.
These lines are also used by "Forgot password" (api) and, **only after the worker is
next recreated**, by the trial-deletion warning email (worker). Today we don't recreate
the worker, so the worker keeps Gmail until its next rebuild.

The new values take effect only when `api` is recreated (step 4). Check SPF/DKIM for
techdeva.net are green in hPanel → Emails first, or codes may go to spam.

## 2. Backup

```bash
TS=$(date +%Y%m%d_%H%M%S); BK=/root/techdeva-backups/email-verify-$TS; mkdir -p $BK
docker compose exec -T db pg_dump -U postgres -d techdeva -Fc > $BK/techdeva.dump
ls -la $BK/techdeva.dump                         # must be > 0 bytes
tar -czf $BK/files.tgz app/main.py app/portal.py web/signup.html web/dashboard.html web/portal.html techdeva-web/src/app/login/page.tsx
cp .env $BK/env.bak && chmod 600 $BK/env.bak      # do not print it
echo $BK
```

## 3. Migration (database `techdeva` only)

```bash
cp $W/db/79_email_codes.sql $L/db/
docker compose exec -T db psql -U postgres -d techdeva -v ON_ERROR_STOP=1 -q < db/79_email_codes.sql
# expect: NOTICE: verify ok: email codes ready; N existing user(s) marked verified
docker compose exec -T db psql -U postgres -d techdeva -qc \
  "INSERT INTO _td_migrations(name) VALUES ('79_email_codes.sql') ON CONFLICT DO NOTHING"
docker compose exec -T db psql -U postgres -d techdeva -Atc \
  "SELECT count(*) FILTER (WHERE email_verified_at IS NULL) AS unverified, count(*) FROM users"
# unverified must be 0
```

Safe on the old code: it only adds tables and fills a column the old code reads for
the dashboard banner (the banner goes away for everyone).
(Note: live `_td_migrations` stops at 74; 75–78 are applied but were never marked. Do
**not** run `ops/verify/deploy.sh` today: it walks every migration and rebuilds more than api.)

## 4. API

```bash
cp $W/app/email_codes.py $W/app/main.py $W/app/portal.py $L/app/
cp $W/tests/test_email_codes.py $L/tests/
docker compose build api
docker compose up -d --no-deps api
sleep 5; docker compose logs --tail=30 api          # no traceback
```

Checks (all must pass; none of them sends email or touches a real account):

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://api.techdeva.net/health          # 200
curl -s -X POST https://api.techdeva.net/auth/resend-code -H 'Content-Type: application/json' \
     -d '{"email":"nobody-deploycheck@example.org"}'                              # 200 {"ok":true,"message":"If that email ..."}
curl -s -X POST https://api.techdeva.net/auth/verify-email -H 'Content-Type: application/json' \
     -d '{"email":"nobody-deploycheck@example.org","code":"000000"}'              # 400 {"detail":"invalid or expired code"}
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://api.techdeva.net/auth/login \
     -H 'Content-Type: application/json' -d '{"email":"nobody-deploycheck@example.org","password":"wrong-password-1"}'   # 401
```

Then **log in once yourself** with your normal founder/owner account at
portal.techdeva.net/login (the old page still works). It must work exactly as before.

Mail test (sends exactly one email, to admin@techdeva.net; prints only OK or the error type):

```bash
docker compose exec -T api python -c "
from app.account import send_mail, mail_configured
assert mail_configured(), 'mail not configured'
try:
    send_mail('admin@techdeva.net', 'TechDeva test code: 123456', 'Test from the deploy. Your TechDeva verification code is:\n\n    123456\n\nIt expires in 10 minutes.\n\nIf this wasn\'t you, ignore this email.\n\nTechDeva')
    print('OK sent')
except Exception as e:
    print('FAILED', type(e).__name__)
"
```

Check the admin@techdeva.net inbox (and spam). If it fails: **stop here** and fix
`.env` (step 1) and recreate api again before going on. Signup and login stay usable:
the API never pretends a send worked, and the user can resend.

## 5. Portal login page (web)

```bash
cp $W/techdeva-web/src/app/login/page.tsx $L/techdeva-web/src/app/login/page.tsx
docker compose build web
docker compose up -d --no-deps web
sleep 5
curl -s -o /dev/null -w '%{http_code}\n' https://portal.techdeva.net/login     # 200
```

Log in once more with your own account: must go straight to the dashboard (no code step,
because the backfill made you verified).

## 6. Signup page + the two other sign-in pages (last; live immediately)

```bash
cp $W/web/signup.html $W/web/dashboard.html $W/web/portal.html $L/web/
curl -s https://api.techdeva.net/ | grep -c 'id="vf"'                           # 1
grep -c gateCode $L/web/dashboard.html                                          # >= 2
grep -c showCodeStep $L/web/portal.html                                         # >= 2
```

Log in once more with your own account on the dashboard sign-in: must go straight in (backfilled).

End-to-end with a throwaway account (optional but recommended): sign up at
https://api.techdeva.net/ with an inbox you control, get the code, confirm, log in at
portal.techdeva.net/login. Afterwards delete that test business from the founder
console (or ask Claude to delete exactly that one tenant, by its id).

## 7. Finish

```bash
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'techdeva-platform|instantgrill'
# compare with /root/pre-deploy-uptimes.txt: ONLY techdeva-platform-api-1 and
# techdeva-platform-web-1 may show a new "Up x seconds". instantgrill-new, db, redis,
# worker must show the same uptime as before.
curl -s -o /dev/null -w '%{http_code}\n' https://new.instantgrill.shop/          # same as before
cd $L && git add db/79_email_codes.sql app/email_codes.py app/main.py app/portal.py \
  web/signup.html web/dashboard.html web/portal.html techdeva-web/src/app/login/page.tsx tests/test_email_codes.py DEPLOY.md
git commit -m "Email verification by 6-digit code: signup sends a code, login waits for it; existing users backfilled verified"
# never push
```

## Rollback

Code first (that alone restores the old behaviour; the new tables are harmless to old code):

```bash
cd $L
git checkout b3d3956 -- app/main.py app/portal.py web/signup.html web/dashboard.html web/portal.html techdeva-web/src/app/login/page.tsx
rm -f app/email_codes.py tests/test_email_codes.py
docker compose build api && docker compose up -d --no-deps api
docker compose build web && docker compose up -d --no-deps web
curl -s -o /dev/null -w '%{http_code}\n' https://api.techdeva.net/health        # 200
```

(`web/signup.html`, `web/dashboard.html` and `web/portal.html` are restored the moment it is checked out, so do it together with api.)

Database, only if needed (un-marks exactly the users the backfill marked; anyone who
verified with a link before keeps it):

```bash
docker compose exec -T db psql -U postgres -d techdeva -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
UPDATE users u SET email_verified_at = NULL
  FROM email_verification_backfill b
 WHERE u.id = b.user_id AND u.email_verified_at = b.verified_at;
DROP TABLE IF EXISTS email_verification_backfill;
DROP TABLE IF EXISTS email_codes;
DELETE FROM _td_migrations WHERE name = '79_email_codes.sql';
COMMIT;
SQL
rm -f db/79_email_codes.sql
```

Mail settings: `cp $BK/env.bak $L/.env`, then `docker compose up -d --no-deps api`.
Last resort (only if a migration went wrong):
`docker compose exec -T db pg_restore -U postgres -d techdeva --clean < $BK/techdeva.dump`.

## Known gaps

- `web/dashboard.html` and `web/portal.html` got the code step too (added after the first build; tested
  in a browser against a stubbed API: verified user goes straight in, unverified user gets the code
  step, wrong/short code errors, resend countdown, back link). The founder console
  (`web/techdeva.html`) is unaffected (founder accounts are backfilled).
- Between step 4 and step 5 (the web build takes a few minutes), a brand-new unverified
  account trying the old portal login page sees "Could not sign in". Signups are rare; do 4 → 6 in one go.
- The verify/resend throttles per IP and per email are in memory (like the login throttle). The
  5-tries-per-code limit and the resend limits are in the database.
