# Part 15 — closing the SaaS gaps

Asked: "do all of it … and make them 100%". Built and proven on a local copy of the stack
(Postgres 16 with the same migrations and RLS, the real API, a real browser). **Not yet
live**: it goes live with the deploy below, then `live_check.py phase7` proves it on the server.

## What was missing, and what exists now

| Gap | Now | Proof (local) |
|---|---|---|
| No "forgot password" | **Forgot password?** on the dashboard sign-in, then `/reset`. One-time link, one hour, dies when the password changes. Emailed when SMTP is set; otherwise it waits in founder console **Requests**, and Faheem presses **Make link** and sends it on WhatsApp. The same answer is given for unknown emails. | request → link → new password → login 200; link reused → "already been used"; staff/client cannot read the queue (403); SMTP path unit-tested |
| Setting a password answered **500** (pre-existing bug) | `/auth/set-password` called `record()` wrongly and never imported it: the password was saved, then the request crashed. Fixed. | set-password → `{"ok":true}`, audit row `auth.password_set` |
| No Posts page, no scheduling | **Posts** (menu AI / Automation): write, add a picture, tick FB/IG, save; then **Post now** or **Schedule** (date and time, up to 90 days); **Cancel schedule** returns it to a draft. The worker publishes due posts, each in its own business's session. If Facebook was disconnected meanwhile, the post is marked "Not posted" with the reason. | scheduled → worker published (fake Graph got `/p1/feed`), audit `social.publish` by scheduler; past time/90+ days/garbage refused; staff 403; another business's scheduled post did **not** use QA's Page |
| Clients could not see or pay their bill | **Plan & Billing** (owner only): trial days left, monthly fee, bills TechDeva *sent*, how to pay, **I have paid** (method + transaction ID). Founder **Requests**: **Money arrived** records the payment, marks the bill paid, trial → active; **Not found** tells the client why. **Invoices out**: **Mark paid** for money that arrived without a claim. A reminder shows on the dashboard in the last 7 days of the trial. | full loop in the browser; duplicate claim refused; another business claiming QA's bill → 404; drafts hidden from the client |
| A new client's WhatsApp agent was set up by hand | **WhatsApp Connection** page: a business with no agent asks for one (owner number, name, assistant). Founder **Requests → Approve**. `ops/agent-template/provision-queue.sh` (cron, on the server) builds it with `new-agent.sh` within a minute and reports building → ready / failed. Then the owner sees Connect WhatsApp. It waits by itself if free memory < 1.5 GB or 15 template agents already exist. | request/approve/fail/retry/ready all run through the script with a stand-in Docker; names cleaned in the API **and** in `new-agent.sh` (quotes, `&`, `$()` refused) |
| pairwatch cron never installed | `ops/agent-template/install-cron.sh` installs pairwatch + provision-queue once, keeps the rest of root's crontab | ran twice against a stand-in crontab: added once, existing line kept |
| Meta App Review needs policy pages | `/privacy`, `/terms`, `/data-deletion` (form lands in Pipeline as "delete my data"); links in every footer. Submission steps in `docs/META-APP-REVIEW.md`. | 6 pages × 3 widths, no overflow; form stores the request |
| Worker never redeployed | `deploy.sh` now rebuilds and restarts **worker** too (it publishes scheduled posts). Still never db, redis or hermes-*. | `bash -n` |

Database: `db/32`–`35`, additive only (new tables, one nullable column, one wider status
check). Every new table has forced RLS: payment claims and agent requests are tenant-isolated;
reset requests and platform settings are admin-only.

## Deploy (on the server)

```
cd <platform folder>
git fetch origin claude/universal-dashboard-production-fe1q9e && git merge --ff-only FETCH_HEAD
bash ops/verify/deploy.sh web          # backup, migrations 32-35, rebuild api + worker + web
python3 ops/verify/live_check.py phase7
bash ops/agent-template/install-cron.sh   # once: pairwatch + agent builder
```
Then, in the founder console, **Requests → How clients pay you**: type your bank / JazzCash
details and Save.

Optional, for emailed reset links: add `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
`SMTP_PASSWORD`, `MAIL_FROM` to `.env` (for example Gmail with an app password,
or Brevo/Mailgun), then `docker compose up -d --no-deps api`.

## Still yours (code cannot do these)

1. **Meta App Review**: submit it (docs/META-APP-REVIEW.md). Until Meta approves and you switch
   to Live, only you and your app testers can connect Facebook.
2. **Prices**: set each client's monthly fee in Clients. The website still says "Talk to us".
3. **Irfan's Send button**: needs his agent re-created on the template. The hard rule forbids me
   touching hermes-agent-irfan; schedule it when the clinic is quiet.
4. **Card/online payments**: not built. No common card gateway serves Pakistan without a
   merchant account in your name (JazzCash/Easypaisa merchant, or a bank gateway). Payments
   now work by transfer + "I have paid" + your confirmation.
5. **Trial end**: nothing is locked when a trial ends (the client sees the notice). Whether to
   lock it, and when, is your call.
6. **Legal text**: the privacy policy and terms describe what the platform actually does; have
   them read by someone qualified before you rely on them.
