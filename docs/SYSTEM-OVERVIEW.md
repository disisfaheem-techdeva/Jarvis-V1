# TechDeva AIBOS: complete system overview

**For:** Faheem (founder, TechDeva) · **Written:** 3 October 2026 · **Code at:** branch `claude/universal-dashboard-production-fe1q9e`

This document explains everything we have built: the technology, where it runs, how it is deployed, every feature, what the audit of 3 October 2026 found, the bugs that remain, and what to improve next. Facts come from the code and from the audit itself. Anything that could not be checked is marked **not verified**.

---

## 1. In one paragraph

TechDeva AIBOS is one platform that runs many service businesses (clinics, salons, academies), each completely separated from the others. Every business gets:
- a **dashboard** (CRM, appointments, money, team, posting, ads, AI assistant);
- its own **web address** (name.techdeva.net);
- its own **WhatsApp AI agent**, built automatically.

TechDeva runs everything from a **founder console**: clients, money, sales team, growth proof. Everything lives on one Hostinger server and is deployed from GitHub with a single command.

---

## 2. Where it lives

| What | Where |
|---|---|
| Server | Hostinger VPS **72.61.203.194**, Ubuntu 24.04, ~96 GB disk (about 32% used on 3 Oct), memory about 61 to 65% used |
| Code | GitHub `disisfaheem-techdeva/techdeva-platform`, branch `claude/universal-dashboard-production-fe1q9e` |
| Code on the server | `/opt/techdeva-platform/techdeva-platform` |
| Settings and secrets | `/opt/techdeva-platform/techdeva-platform/.env` (never in GitHub). Holds the database password, session secret, Gemini key, Meta/Google app keys, internal agent key, SMTP |
| WhatsApp agents' files | `/docker/hermes-<agent-key>/` (one folder per client agent) |
| Backups made by every deploy | `/root/backups/part14-<date-time>/` (database dump + the code version before the deploy) |
| Post videos | Docker volume `mediadata`, mounted at `/srv/media` in the api and worker. **Not in the database backup.** |

### Web addresses

| Address | What it is |
|---|---|
| `api.techdeva.net` | The platform: server API, client dashboard (`/dashboard`), founder console (`/techdeva`), login, PIN login, signup, onboarding |
| `<name>.techdeva.net` | Each client's own address (automatic). DNS: wildcard `*` A record at Hostinger. HTTPS certificates: Let's Encrypt via Traefik |
| `drirfan.techdeva.net` | Dr Irfan's address (set by hand) |
| `portal.techdeva.net` | The public website and portal (Next.js): pricing, solutions, privacy, terms, data deletion, signup |
| `crm.techdeva.net` | **Separate system:** the clinic's own CRM (`/opt/beauty-dashboard`). Not part of this platform and never touched by it |
| `techdeva.net` | **Separate:** the WordPress marketing site on Hostinger shared hosting |

---

## 3. Technology (the stack)

### Backend
- **Python 3.12** with **FastAPI 0.115** and **Uvicorn**: about 15,000 lines in `app/` and **208 routes**.
- **PostgreSQL 16** with **71 tables** and **55 numbered migrations** in `db/`.
  - Every business table uses **forced Row-Level Security**, so one business can never read another's rows.
  - The app connects as `techdeva_app`, which is not a superuser and cannot bypass these rules (re-checked in the audit).
- **Redis 7** for the queue and caches.
- **Worker** (`app/worker.py` + `app/scheduler.py`): the same code as the API, running jobs on a timer:
  - scheduled posts;
  - the ad autopilot;
  - deleting old trial data;
  - queue recovery.
- **psycopg 3**, **requests**, **cryptography** (AES-GCM: connected Facebook/Google tokens are encrypted in the database), **qrcode**, **BeautifulSoup**.
- **ffmpeg / ffprobe** (in the API image): checks and trims post videos.
- **Audit log:** every important change is written to the `executions` table (who, what, when).

### Frontend
- **Client dashboard** `web/dashboard.html`: a single page in plain JavaScript (ES5, no framework), served by the API. About 7,000 lines, built in 27 "parts" layered on top of each other.
- **Founder console** `web/techdeva.html`: a single page in plain JavaScript (ES6), violet and black theme.
- **Small pages:** `signup.html`, `onboarding.html`, `pin.html` (staff PIN login), `reset.html` (password), `portal.html`.
- **Public portal** `techdeva-web/`: **Next.js 16.3, React 19, Tailwind 4, TypeScript**. 27 pages: home, pricing, solutions, demo, case studies, privacy, terms, data deletion, login, set-password, and the portal and founder pages.
- The dashboard and console files are mounted into the API container, so a change to them goes live as soon as it is pulled. Server changes need a rebuild.

### AI
- **Google Gemini**, through the key `GEMINI_API_KEY` in `.env`:
  - Jarvis (text and voice);
  - post captions;
  - campaign writing;
  - advisor wording;
  - AI pictures (image models `gemini-2.5-flash-image`, then `gemini-3-pro-image-preview`);
  - Jarvis's spoken replies.
- The code can also use Anthropic or OpenAI (`AI_PROVIDER`, `AI_MODEL_CHAIN`).
- **WhatsApp agents:** the **Nous Research Hermes agent** (`nousresearch/hermes-agent`), customised by our template.
  - Model: Gemini `gemini-3.8-flash`, with fallbacks `gemini-2.5-flash-lite` and `gemini-3-flash-preview` (seen in the build log).
  - Personality: written to `SOUL.md` per business.
  - WhatsApp connection: a Baileys **WhatsApp Web bridge**.
  - Pairing helper: a **pairctl** sidecar (Node 22).

### Outside services
| Service | Used for |
|---|---|
| Meta Graph API v21 | Facebook Page and Instagram posting (pictures, carousels, videos, Reels), ad accounts, live ad results, launching and pausing campaigns |
| Google Ads API v21 | Google ad results, Search campaigns |
| Google Gemini | All AI (above) |
| Let's Encrypt (through Traefik) | HTTPS certificates |
| Hostinger DNS | `techdeva.net` records, including the wildcard |
| SMTP (optional) | "Forgot password" and trial warning emails |
| JS Bank, JazzCash, Easypaisa | Manual payments: the client presses "I have paid", the founder confirms |

---

## 4. What runs on the server (Docker containers)

| Container | What it does |
|---|---|
| `traefik-traefik-1` | Front door: HTTPS and routing for every address |
| `techdeva-platform-api-1` | The platform API and the dashboards |
| `techdeva-platform-worker-1` | Background jobs |
| `techdeva-platform-db-1` | PostgreSQL 16 |
| `techdeva-platform-redis-1` | Redis 7 |
| `techdeva-platform-web-1` | The Next.js public portal |
| `techdeva-tenant-routes` | A tiny container that only carries the routing for every `<name>.techdeva.net` |
| `hermes-agent-<key>` + `pairctl-<key>` | One pair per client: the WhatsApp AI agent and its pairing and inbox helper |
| `hermes-agent-irfan`, `pairctl-irfan`, `hermes-workspace-ofsd-*` | **The clinic's own Hermes/Zara: hand-built, never touched by the platform's scripts** |

### Timed jobs on the server (root crontab)
| Every | Script | Does |
|---|---|---|
| 1 minute | `ops/agent-template/provision-queue.sh` | Builds approved WhatsApp agents, removes agents of deleted trial businesses, and every 5 minutes pauses and restarts agents for the free-trial message limit |
| 1 minute | `ops/agent-template/pairwatch.sh` | Restarts an agent once after its number is paired |
| 2 minutes | `ops/setup/sync_subdomains.sh` | Puts each client's own address live |
| Not verified | `scripts/backup_db.sh` | Nightly backup script exists. **Whether it is scheduled was not verified.** Every deploy makes its own backup |

---

## 5. How we build and deploy

1. Claude builds and tests on a copy of the platform: a local database, a local API, a real browser, and fake Facebook, Google and Gemini servers. Test data is deleted afterwards and the counts are checked.
2. Claude commits and pushes to GitHub (branch above).
3. Faheem runs on the server:
   ```
   cd /opt/techdeva-platform/techdeva-platform && git fetch origin claude/universal-dashboard-production-fe1q9e && git merge --ff-only FETCH_HEAD && bash ops/verify/deploy.sh
   ```
   `deploy.sh` makes a backup, applies new database changes, rebuilds **only** api and worker, restarts them, checks `/health`, and prints the exact rollback commands. Adding `web` also rebuilds the Next.js portal.
4. Live checks: `python3 ops/verify/live_check.py phaseN`: 22 phases and 162 checks of real behaviour. Each uses a test business and puts its data back afterwards.
5. Rules (CLAUDE.md):
   - protect the clinic and Hermes above everything;
   - no secrets in GitHub;
   - never delete real business data;
   - prove every fix;
   - money and pricing decisions are Faheem's.

---

## 6. Everything the platform does

### 6.1 Client dashboard (31 pages)
**Dashboard and Jarvis**
- Overview with live figures.
- **Jarvis**, a text and voice assistant (Urdu/English) that answers from the business's own data and *does* things after a "yes":
  - books appointments;
  - records expenses;
  - schedules posts (with pictures and a proper caption);
  - opens pages;
  - changes settings ("hide inventory", "my colour is #C9A227", "teach my agent this price").

**CRM**
- Leads (with source and ad click tracking).
- Customers (history, edit, close).
- Appointments and calendar (packages, sessions, payment, balance).
- WhatsApp Inbox: read every chat and reply from the dashboard.

**Business**
- Services, products and packages (create, edit).
- Team.
- Attendance.
- HR (editable rows, salary).
- Business settings: logo, colour, hours, hide sections.

**Finance**
- Revenue.
- Expenses.
- Invoices and payments (client and walk-in name, print).
- Daily closing.
- Profit & loss.

**Operations**
- Inventory (stock movements, edit).
- Daily entry (edit client, services, prices, discount).
- Reports.

**AI / automation**
- AI agents.
- WhatsApp agent: request it, then a QR self-connection, multiple numbers.
- Needs Human: approvals waiting.

**Posts**
- Facebook and Instagram, with a **Page picker** (no guessing when a login has many Pages).
- Up to 10 pictures, posted as a Facebook album and an Instagram carousel.
- Schedule for later (keeps the time you picked; asks you to confirm).
- **Picture designer:**
  - Instagram sizes;
  - headline and text on a band in the brand colour;
  - the logo;
  - layouts: Offer, New service, Before/After.
- **AI pictures**, described in words: 20 a day on a plan, 5 in the trial.
- **Videos:** MP4/MOV up to 100 MB, trimmed and posted as a Facebook video and an **Instagram Reel**.

**Campaigns (ads)**
- Live Meta and Google ad results.
- Ad account picker and Facebook Page picker.
- **Ad autopilot** (Pro plan):
  - writes campaigns (text, hashtags, keywords, picture);
  - launches with the owner's OK;
  - auto-pauses ads that cost too much per result;
  - may launch by itself only within the owner's daily budget limit.

**Growth Advisor** (Pro plan)
- Weekly plain tips from the business's own numbers, with action buttons: pause this campaign, write this campaign.

**Integrations**
- Connect Facebook and Instagram, and Google (OAuth).
- Choose the ad account.

**Plan & billing**
- Plan, usage (WhatsApp conversations per month), invoices, pay ("I have paid" plus transaction ID).
- **Free trial: 7 days, 50 customer messages for the agent and 5 AI pictures.** Then the agent pauses until payment; after payment the chosen plan applies.

**My account (👤)**
- Change your name and your password.
- The owner can rename the business.

**Logins**
- Owner: email and password.
- Staff: **PIN login** on the business's own address, with a typed business code if needed.
- Forgot password.

### 6.2 Founder console (21 pages)
- **Jarvis for any one client**, kept strictly separate per client.
- **Overview**, **Requests** (WhatsApp agent requests, now approved automatically), **Clients**, **Connections**, **Pipeline & trials**.
- **Bookings**, **AI workforce**, **Waiting on you**.
- **Money:**
  - **Growth proof:** each client's sales before and after TechDeva, the growth, your profit share, and a printable before/after **one-pager** for selling (hides the client's name and amounts by default);
  - My revenue;
  - Client business;
  - Invoices out;
  - plan prices.
- **TechDeva team and services:**
  - **Sales team:** operators' contacts per day against target, leads, follow-up reminders, meetings in person or online, clients won, salaries (founder only);
  - Team;
  - Services catalogue;
  - Client services;
  - Client work (delivery tasks).
- **Platform:** Health, Incidents, Trial data and server.
- **My account** (name, password).

### 6.3 Automatic behind the scenes
- New signup gives the business, its own address, its dashboard and its WhatsApp agent, all built automatically (up to 5 a day, with memory checks so the clinic's server never runs out).
- WhatsApp agent template:
  - Gemini only, with a bounded set of tools;
  - personality and voice rules;
  - the bridge passes the QR code to the dashboard;
  - eight checks (T1 to T8) must pass before an agent is ready.
- Scheduled posts go out on time; videos and Reels are posted in the background.
- Old video files are cleaned up: after 2 days if unused, 30 days after posting.
- Trial data is deleted 15 days after an unpaid trial, with a warning 3 days before.
- The founder is alerted to buy a new server at 8 paying clients.
- The free-trial message limit pauses and restarts agents automatically.
- **Separation of businesses** is enforced in the database, not just on screen. The founder account is fenced off from every client business.

---

## 7. Audit, 3 October 2026 (what was actually tested)

| Check | Result |
|---|---|
| All 22 live-check phases on a full copy (162 checks) | **153 passed.** The 9 failures were caused by the copy (no internet to Facebook, fake AI answers, data only on the real server), not by the code. Those phases passed on the live server earlier |
| Unit tests | **22 of 22 passed** |
| Database separation | **Every** business table has forced Row-Level Security with a policy; the app role has no superuser or bypass rights |
| Every server page without a login | Only public pages and the price list open; **every change needs a login** except Sign out |
| Every server request sent empty | **No crashes** (no 5xx), and no errors in the server log during the whole browser run |
| Every page in a real browser | Founder console (21 pages): **no problems.** Owner (31 pages): no real problems. Staff on a phone: **1 real bug** (below) |
| 31 dashboard pages on a phone | **None** wider than the screen |
| Live server checks (output Faheem sent) | Deploys healthy (`/health` 200); video tool and video storage present; client addresses on HTTPS with Let's Encrypt; WhatsApp QR and pairing working; Instagram posting working |

---

## 8. Bugs still open

### Code bugs (confirmed)
1. **Staff see broken money pages.** Revenue, Reports, Growth Advisor and the money tiles on the Dashboard page are open to staff. The server correctly refuses them the figures, but the page says "the reporting service did not respond" instead of "owner only". *Fix: hide them for staff, or show the owner-only message.*
2. **Changing a password does not sign out other devices.** Old logins stay valid until they expire. *Fix: "sign out everywhere".*
3. **The older test files** in `tests/` point at the live server and cannot be run safely. Test debt, not a user-facing bug.

### Waiting on an account or a decision (not code)
4. **AI pictures:** the Gemini key has no image allowance (error 429). Turn on billing for that key's project in Google AI Studio, then run `bash ops/agent-template/update-agent-key.sh` if the key changes.
5. **Facebook "App not active"**: the Meta app must go Live and pass App Review (`docs/META-APP-REVIEW.md`, `docs/COWORK-META-APP-BRIEF.md`).
6. **Pages mixed:** Dr Irfan's Facebook connection holds 10 Pages, including TechDeva's own. Reconnect with only the clinic's Pages ticked.
7. **Growth proof is empty** until each client's "sales per month before TechDeva" is entered (Terms button).
8. **The server needs a restart:** 24 updates waiting, "System restart required". Plan it for a quiet hour, because the clinic runs on the same machine.
9. **Not verified live:** a real video or Reel posted to Facebook and Instagram; the automatic trial pause on the real server; Jarvis's actions with the real AI on the newest code.
10. **Decisions open:**
    - which plans include videos and AI pictures;
    - profit-share % per client;
    - USD prices;
    - whether a sale counts as "won" when the operator marks it or at first payment;
    - listen-only WhatsApp for operators' work numbers;
    - the call bot's phone number type.

---

## 9. What to improve next (in order)

1. **Fix the staff money pages** (bug 1). Small and quick.
2. **Enter the clinic's "before" sales and the profit-share %.** Growth proof is the number TechDeva sells and bills on.
3. **Meta: go Live and pass App Review.** Until then, clients outside test accounts cannot connect Facebook.
4. **Turn on Gemini billing**, then test AI pictures once.
5. **Separate the Facebook Pages** between the clinic and TechDeva.
6. **Plan the server restart** and **add the video storage to backups**. Confirm the nightly database backup is scheduled.
7. **"Sign out everywhere"** after a password change.
8. **A Rebuild button for WhatsApp agents** in the founder console. Today a rebuild needs a server command.
9. **Split the dashboard code into modules.** One very large file with 27 layered parts works today, but each change gets riskier.
10. **Replace the old live-server tests** with tests that run on a copy, so every change can be checked automatically.
11. **Later:** the AI call bot (after the clinic's growth is proven); a WhatsApp Business API option for clients who want calls and templates; a second server once 8 clients are paying.
