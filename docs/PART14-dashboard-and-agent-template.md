# PART 14 — Universal client dashboard + new-client WhatsApp agent template

Date: 28–29 Sep 2026 · Branch `claude/universal-dashboard-production-fe1q9e` · Server 72.61.203.194
Proof tenant: **TechDeva QA Test Co** (every write test ran there, deleted its own rows, and proved the counts were back).
Dr Irfan's tenant (`605e1cf0-…`) was only ever **read**, except one send-probe audit row that was deleted and counted back (0 → 0).
Protected containers (`hermes-agent-irfan`, `pairctl-irfan`, `hermes-workspace-ofsd-*`) were never restarted or re-created:
`prove.sh` compared their container ids and start times before and after — identical.

Evidence files on the server: `/root/phase1-check.txt`, `/root/phase2-check.txt`, `/root/phase3-check.txt`,
`/root/phase4-check.txt`, `/root/phase5-check.txt` (65 checks, 1 fail = Meta secret), `/root/phase6-prove.txt`.
Re-run any time: `python3 ops/verify/live_check.py phase1 phase2 phase3 phase4 phase5` and `bash ops/agent-template/prove.sh`.

---

## 1. PASS / FAIL / BLOCKED — every item

| # | Item | Result | Evidence |
|---|---|---|---|
| A1 | portal Log in → `/auth/login` 404; `/dashboard` 404 | **PASS** | `/auth/login` → 307 `/login`; homepage links `/login`; `/dashboard` → api dashboard |
| A2 | Customers: no Source, row click did nothing | **PASS** | Source on add/edit, row opens history with edit/delete |
| A3 | "sohail gandu" (03124566695) test record | **LEFT FOR FAHEEM** | not touched |
| A4 | 0 appointments vs 1 invoice ₨5,000 | **PASS (cause found)** | Irfan's `INV-0001` was written at 15:04 by the old Daily Entry as a bare bill with **no appointment and no customer**; now listed under "Bills with no appointment" |
| A5 | Services: 6 onboarding services missing, no Edit | **PASS (code)** / **needs one click** | services table is the single list; "Save them as services" button (owner). Not clicked for Irfan — it writes his real data |
| A6 | Inventory had no Add Product | **PASS** | add product + opening stock 12, sale 2 → 10 on hand after refresh |
| A7 | WhatsApp labels "swapped" | **PASS** | not a swap: the agent stores its own `[System: …]` retry notes as role `user`; they quote the agent, so agent text showed as "Customer". Now shown as "Internal note to the AI (customer never saw this)" — live chat re-read shows `system` role |
| A9 | `/incidents` about once a second | **PASS** | 0 calls in 12 s across 6 sections; at most 1/min, only while the tab is visible |
| A10 / #2 / #9 | Daily Entry every button, CRUD against the DB | **PASS** | add, save, view/edit, delete (unpaid bill voided, not erased), cancel, close, search, status filter, date; survives refresh |
| #1 | Appointment fields + 20,000 − 12,000 = 8,000 | **PASS** | customer, phone, service, staff, location, date, start/end, price, paid, balance, currency, notes, status, package, sessions read back live; edit persists |
| #3 | Customer history | **PASS** | appointments, services/bills with lines, payments, balance, packages, staff, source, dates, notes, WhatsApp chats matched by phone |
| #4 | Customer source | **PASS** | WhatsApp/Facebook/Instagram/Website/Google/Walk-in/Referral/Phone/Other, survives refresh |
| #5 | WhatsApp reply box + real Send | **PASS (platform)** / **BLOCKED for Irfan** | reply box, Sending/Sent ✓/Not sent + reason, kept after refresh (tested with the real pairctl code). Irfan: refused honestly — "no send route yet (older pairctl)"; his pairctl and agent are on different Docker networks and the bridge is loopback-only |
| #6 | Services add/view/edit/delete, FK-safe | **PASS** | a service inside a package cannot be deleted (409, "mark it inactive") |
| #7 | Products add/view/edit/delete | **PASS** | product with stock history cannot be deleted; deactivate instead |
| #8 | Expenses add/view/edit/delete | **PASS** | edit/delete persist after re-read |
| #10 | Packages | **PASS** | create, edit price, add/edit/remove items, delete unsold; line must be exactly one service or product, qty ≥ 1 |
| #11 | Rights enforced by the API | **PASS** | operator gets 403 on catalogue/prices, staff, locations, policies, P&L, HR, expense edit/delete, customer delete, appointment delete, pins, services import |
| #12 | Inventory add product → DB → display | **PASS** | frontend, API, validation (empty/duplicate/negative), RLS |
| #13 | Team → Pin to Dashboard per role | **PASS** | saved in `dashboard_prefs.pinned_by_role`, shown per role, operator 403 on change |
| #14 | Jarvis LISTEN/STOP + states | **PASS** | Idle → Listening → Processing → Responding/Error; mic-blocked message; live AI answer + server voice audio (phase5) |
| #15 | Jarvis UI polish | **PASS** | orb + state label + single toggle, reuses existing voice helpers |
| #16 | Facebook "Failed token exchange — 400" | **PASS** | root cause: wrong `META_APP_SECRET` (34 chars; Meta refused with code 1 / 190). Replaced 28 Sep → diagnose `app_check: ok` (app "TechDeva AIBOS") → real connect succeeded: "✓ Facebook & Instagram connected!", ad account 180347302012051 |
| #17 | Sidebar jumping | **PASS** | scrollTop 400 → 400 across navigation and data redraws |
| #18 | Sidebar groups | **PASS** | Dashboard · CRM · Business · Finance · Operations · AI/Automation · Administration (only existing sections; no Payments or Rights page exists — Payments live inside Invoices) |
| #19 | Responsive | **PASS** | 28 sections × 1366/1440/1920/320/360/375/390/400/430 + appointment modal + drawer: 0 horizontal overflow |
| #20–21 | Don't break / regression / protect data | **PASS** | full live regression 65 checks; QA counts back to 0; unit tests pass |
| T1 | Tool lockdown | **PASS** | probe agent: 10 toolsets disabled; `hermes tools` shows them ✗ |
| T2 | Gemini chain, no OpenRouter | **PASS after fix c7e0922** | probe found a fresh config with `model.base_url=openrouter.ai`; now stripped and verified; Groq/AWS/OpenRouter/OpenAI keys blanked |
| T3 | Status-message leak, survives re-create | **PASS** | patched at image build (`techdeva/hermes-agent:<base>-td-<files fingerprint>`); line 395 `== "whatsapp": return None` in the running probe; `tool_progress: off` |
| T4 | Home channel per tenant | **PASS** | `WHATSAPP_HOME_CHANNEL=<owner>@s.whatsapp.net` + `_NAME` |
| T5 | SOUL voice rules | **PASS** | block present (female Zara, "aap", 2–4 lines, one follow-up question) |
| T6 | Pairing pickup | **PASS** | pairctl shares the agent network (`container:<agent id>`); pairing marker → pairwatch restarted the agent once; second run did nothing |
| T7 | Throwaway agent proven and removed | **PASS** | leftovers 0/0/0; protected containers identical |

---

## 2. Issue → root cause → file → fix

| Issue | Root cause | File(s) | Fix |
|---|---|---|---|
| A1 login 404 | homepage linked `/auth/login` (the API's POST) | `techdeva-web/src/app/page.tsx`, `next.config.ts` | link `/login`; redirects for `/auth/login`, `/dashboard` |
| A4 invisible visit | old Daily Entry created a bare invoice | `web/dashboard.html`, `app/booking.py` | Daily Entry books through `/appointments/book`; `include_unlinked` lists old bare bills |
| Appointment fields lost | end time, location, staff, currency, package not stored/returned | `app/booking.py` | stored on appointment/invoice/lines, returned by `/appointments/list` |
| Money could be orphaned | generic `/business/*` allowed deleting appointments/payments | `app/business.py` | money/ledger tables must use dedicated endpoints (405); in-use rows refused (409) |
| 500 on duplicates | DB errors surfaced raw | `app/business.py` | readable 400/409 |
| A5 two sources of truth | onboarding list only shown when the table was empty | `app/business.py`, dashboard | `pending_onboarding` + idempotent import |
| #11 rights | generic router had no role checks | `app/business.py` | `OWNER_ONLY` table |
| A7 labels | agent stores `[System: …]` nudges as `user` | `app/whatsapp.py`, dashboard | relabelled `system`, shown collapsed |
| #5 no reply | no send route anywhere | `app/whatsapp.py`, `ops/pairctl/pairctl.mjs`, dashboard | `POST /whatsapp/chats/{id}/send` → pairctl `/send` → agent bridge; audit-backed history |
| #16 opaque 400 | Meta's error body thrown away; secret wrong | `app/oauth.py`, `app/main.py` | Meta's reason shown + stored; `/oauth/meta/diagnose`; secret/redirect normalised |
| A9 polling | `incidentBar()` called on every redraw | dashboard | 60 s, visible-only, cached |
| #17 jump | shell rewrote the sidebar on every redraw | dashboard `renderShell` | keep sidebar/page scroll |
| T3 lost on re-create | stock image filters Telegram only | `ops/agent-template/patch_gateway.py` | applied at image build, assert-guarded |
| T6 missed pairing | gateway disables WhatsApp when started unpaired; pairctl on another network | `pairctl.mjs`, `pairwatch.sh`, compose template | marker + label-scoped restart; pairctl in the agent's netns |

## 3. Facebook (#16) — exact setting to change

Meta refuses our app id + secret for app **894985669798073**: `Invalid OAuth access token signature` (190) on the
app-token check, and Irfan's real attempt was refused with `Error validating client secret` (code 1).
The stored `META_APP_SECRET` is 34 characters; Meta issues 32 lowercase hex. It is not whitespace or quotes (checked).

1. developers.facebook.com → app 894985669798073 → **App settings → Basic → App secret → Show**, copy it.
2. `nano /opt/techdeva-platform/techdeva-platform/.env` → `META_APP_SECRET=<32 chars, no quotes>` → save.
3. `docker compose up -d --no-deps --force-recreate api`
4. `python3 ops/verify/live_check.py phase3` → `app_check: ok`.
5. Facebook Login → Settings → Valid OAuth Redirect URIs must contain `https://api.techdeva.net/oauth/meta/callback`.
6. The app is in **Development** mode: only users under App roles can connect until it is switched to Live (App Review for the requested permissions).

## 4. Changes by layer

- **Frontend** (`web/dashboard.html`): shared appointment modal (Appointments, Daily Entry, customer history); Customers with source/search/history/edit/delete; Inventory add; editable `crudTable` for services, products, expenses, packages, settings; Inbox reply; menu groups; pins; Jarvis; scroll/polling fixes.
- **Backend**: `booking.py` (fields, delete, history), `business.py` (guards, rights, import, readable errors), `whatsapp.py` (send, system labels), `oauth.py` + `main.py` (Meta errors, diagnose), `console.py` (pins).
- **DB**: no schema migration was needed. No real data changed.
- **Integrations**: pairctl `/send`, pairing marker, `can_send` in `/health` (backward compatible; `pairctl-irfan` runs the repo file and will load it on its next restart).
- **Template**: `ops/agent-template/` (image patch, config, SOUL, compose, new/remove/prove, pairwatch).
- **Ops**: `ops/verify/deploy.sh` (backup-first, api/web only), `ops/verify/live_check.py` (QA-only writes, counts proven).

## 5. Remaining blockers and decisions

1. **Facebook** — secret replaced and a real connect succeeded (Faheem's admin account, for testing). Before a client relies on it: remove the test connection on Facebook (Settings → Business integrations → TechDeva AIBOS) and let the client connect with their own account; submit App Review and switch the app to Live so non-role users can connect.
2. **WhatsApp Send for Irfan** — needs `pairctl-irfan` to share `hermes-agent-irfan`'s network (a planned re-create of both, or moving Irfan onto the template). Also brings T3 into an image so it survives. Faheem schedules; nothing was touched tonight.
3. **Irfan's services** — press "Save them as services" on his Services page (6 onboarding services; his existing row is "botox in", 5,000, 15 min).
4. **"sohail gandu"** customer and the ₨5,000 bill `INV-0001` with no customer — Faheem decides keep/void.
5. **Rights table** (`OWNER_ONLY` in `app/business.py`) — cautious default; Faheem may loosen for front desk.
6. **pairwatch for new agents** — add the cron line from `ops/agent-template/README.md` before the first real client is provisioned. The existing `/usr/local/bin/hermes-irfan-pairwatch.sh` was not read or changed.
7. **Enabled agent tools beyond T1** — the probe shows web search, vision, image generation, TTS, skills, todo, memory, session search, clarify still enabled. Faheem decides whether web/image_gen should also be off for client agents.
8. **Gemini quota** — every agent and Jarvis share the platform `GEMINI_API_KEY` unless a per-tenant key is given; watch quota as clients are added.
9. **Server** — "System restart required" is pending on the VPS; a reboot restarts every container (pairctl-irfan then loads the new pairctl code — compatible).
10. **Payments / Rights pages** — not built (the spec said only existing modules); payments are recorded and shown inside Invoices and customer history.
