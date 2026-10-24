# TechDeva system audit and repair (24 Oct 2026)

Done on a local scratch copy of the database (Postgres 16, the app's own
`techdeva_app` role, forced row-level security) with the real API, a real
browser and the repository's tests. **Production was not accessed, nothing was
deployed and no production migration was run.**

## A. Summary

- The platform is in good working order on the scratch copy. Every tab opens
  without a script error, every API path the screens call exists, and the
  whole test suite passes (155 passed, 0 errors).
- Two real defects were found and fixed (B1, B2). The third finding (B3) was
  my own test environment, not the code.
- Earlier today's work (Business Intelligence agent, the CRM in five phases,
  the Dashboard redesign) was checked again end to end in this audit.
- **What stays unproven**, because it needs the live server or real outside
  services:
  - real Facebook and Instagram posting;
  - real AI answers (no AI key on the scratch copy);
  - scanning a real website (the network here is restricted);
  - whether migrations 75, 76 and 77 are applied on production.
- **Verdict:** ready for staging. Production after the deploy steps in I.

### B. Findings this audit

| # | Area | Found | Root cause | Fix | Now |
|---|---|---|---|---|---|
| B1 | Publishing (background) | A post claimed for publishing whose result was never saved (worker restart, or an error after the claim) stayed "publishing" for ever, listed under Scheduled, with no way to retry | Nothing picked up a stale `publishing` row; the reclaim in `publish_post` only ran when someone called it, and the page shows no button for that state | `social.publish_due` (runs every minute) marks a post "publishing" for over 15 minutes as **failed**, with the message "TechDeva did not get Facebook's answer… check your Page; if it is not there, approve it again", and audits it. It never re-posts by itself, because that could post twice. The Posts page already shows the message and the Post now button for failed posts | Test passes; a post 5 minutes old is left alone; Meta is not called |
| B2 | Jarvis isolation | One login acting in two businesses (TechDeva staff) had no test proving histories never mix | n/a: the code was already right (`tenant_session` turns a founder acting for one business into that business's owner, so RLS fences rows); there was only no proof | Test added: in business B, the founder sees none of A's conversations, cannot continue them, and B's Jarvis facts contain nothing of A's | Test passes |
| B3 | Test suite | 11 storefront tests "errored" in every run this session | My scratch database lacked the documented fixture `tests/fixtures/storefront_test_tenant.sql` (a test tenant); not a code defect | Loaded the fixture into the scratch copy only | 14 storefront tests pass |

### Checked and found working (evidence)

| Feature | How it was checked | Result |
|---|---|---|
| All 49 dashboard tabs | Browser opened each as the owner; script errors, failed requests and error text recorded | No script errors. Only non-200s: `/whatsapp` 409 ("No WhatsApp agent has been assigned to this business yet", shown on screen) and `/adpilot` 402 (Pro plan), both honest states |
| Frontend ↔ backend | Every `api('…')` path in `web/*.html` matched against the 310 FastAPI routes and methods | No call to a missing endpoint |
| Main Dashboard | `tests/test_dashboard.py`, `tests/test_dashboard_v3.py`; browser at 1440 dark/light, 1440 without Jarvis, 1024, 390 dark/light; accessibility (axe) check | Figures match the rows made by the tests; WCAG 2.2 AA clean; no sideways scroll; sidebar byte-identical to before |
| CRM | `test_crm.py`, `test_crm_reports.py`, `test_crm_rules.py` (15) | Search, filters, pages, lead page, follow-ups, conversion without duplicates, reports, rules, roles, isolation |
| Website agent | `test_website_agent.py` (20) | robots.txt, sitemap, canonical, page and time limits, private-address and DNS-rebinding refusal, one scan at a time, restart recovery, owner edits kept across rescans, approval feeds Jarvis and captions |
| Jarvis | `test_jarvis_full.py` (13) | Per-person and per-business history, role limits, no change without permission, publishing reply says only what Meta confirmed |
| Create with AI | Caption Writer → `/autopilot/caption` → `_grounding` adds the approved profile (test in `test_website_agent.py`); AI pictures use name and industry only, on purpose (no words or prices in the picture) | Working (with a fake AI) |
| Publishing | `publish_post`: claims first (no double post), status from Meta's answer (`published`/`partial`/`failed`); the database refuses a post with no platform | Correct; B1 fixed the one gap |
| Agent tasks | Scheduler's reaper re-queues work a lost worker left running and raises an alert | Present |

## C–D. Connectivity

| Feature | Frontend | Backend | Database | Outside service | End to end |
|---|---|---|---|---|---|
| Website analysis | PASS | PASS | PASS | NOT TESTED (real sites) | PASS with a local test site |
| Business profile | PASS | PASS | PASS | N/A | PASS |
| Jarvis context | PASS | PASS | PASS | BLOCKED (no AI key locally) | PASS with a fake AI |
| Create with AI | PASS | PASS | PASS | BLOCKED (no AI key locally) | PASS with a fake AI |
| CRM | PASS | PASS | PASS | N/A | PASS |
| Dashboard | PASS | PASS | PASS | N/A | PASS |
| Publishing | PASS | PASS | PASS | NOT TESTED (needs a real Page) | PASS with a fake Meta |

## E. Database

- **No new migration in this audit.**
- 75, 76 and 77 (from earlier today) are additive and applied on the scratch
  copy; their production status is **unknown** from here.
- Duplicate protection:
  - a unique rule key for CRM rules;
  - the publishing claim prevents a double post;
  - lead conversion reuses a matching customer;
  - one website scan at a time per business.

## F. Tests

`pytest tests`: **155 passed, 0 errors** (the run before this audit: 142 passed
and 11 set-up errors, see B3). `sync.py --check` passes. The browser checks are
in the table above. `techdeva-web/` was not changed, so its lint and build were
not run.

## G. Files changed in this audit

- `app/social.py`: stuck "publishing" posts become failed, with an honest message (B1).
- `tests/test_jarvis_full.py`: two tests (B1, B2).
- `docs/SYSTEM-AUDIT-2026-10-24.md`: this report.

## H. Remaining work

| Priority | Item | Why open | Next step |
|---|---|---|---|
| High | Deploy 75, 76, 77 and today's commits | No access to the server from here | Server Claude: back up the database, apply in order, rebuild api and worker, check (section I) |
| High | Real posting test | Needs the real Page connection | After deploy: approve one test post, confirm it on the Page, then delete it |
| Medium | Real AI check of Jarvis, captions and the website profile | No AI key on the scratch copy | After deploy: ask Jarvis one question, write one caption, scan techdeva.net |
| Medium | WhatsApp on the clinic's dashboard copy shows "no agent assigned" | Expected on the scratch copy; check that production shows Hermes as assigned | Look at Chats on production |
| Low | `/favicon.ico` returns 404 | Cosmetic | Add a favicon |
| External | Meta ads: hook 3 stays paused | Waiting for your explicit OK | Say "hook 3 chala do" (go ahead with hook 3) |

## I. Production

Not touched. To deploy (server Claude):

1. `git status` must be clean; pull `claude/sweet-allen-52coh9`.
2. Back up the database. Apply `db/75`, `db/76`, `db/77` in order, only those
   not yet applied. Each prints "verify ok".
3. Rebuild **api and worker** only:
   `docker compose build api worker && docker compose up -d api worker`.
   Stop on any failure.
4. Check:
   - `/health` returns 200;
   - Dashboard, Leads, Follow-ups and Business Profile open;
   - the clinic's lead count is unchanged;
   - the clinic CRM and the Hermes containers are still running;
   - Instant Grill is untouched.

## J. Scorecard

| Workstream | Status |
|---|---|
| Main Dashboard | IMPLEMENTED BUT NOT FULLY VERIFIED (verified locally; not on production) |
| CRM | IMPLEMENTED BUT NOT FULLY VERIFIED (same) |
| Website Integration / BI agent | IMPLEMENTED BUT NOT FULLY VERIFIED (no real site scanned) |
| Jarvis | IMPLEMENTED BUT NOT FULLY VERIFIED (fake AI only) |
| Create with AI | IMPLEMENTED BUT NOT FULLY VERIFIED (fake AI only) |
| Social media integrations | PARTIALLY COMPLETE (logic tested with a fake Meta; real posting not tested) |
| Backend and database | COMPLETE AND VERIFIED on the scratch copy |
| Security (tenant isolation, roles, SSRF, prompt injection, double posting) | COMPLETE AND VERIFIED by tests |
| Testing | COMPLETE: 155 passed, 0 errors |
| Deployment | NOT STARTED (not authorised from here) |
