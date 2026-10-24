# TechDeva CRM upgrade (24 Oct 2026)

Built in phases. Each phase ends with its tests and the project rules checked
before the next one starts. Nothing that existed was removed or re-ordered: the
CRM module keeps its tabs in the same order (Leads, Sales, Client work,
Customers, Pipeline, Appointments, Calendar); new screens are added after them.

## Phase A: audit (read only)

### What exists

| Capability | Where | Status | Notes |
|---|---|---|---|
| Leads list | `GET /leads` (app/main.py), `VIEWS.leads` | Partially working | First 200 rows only, no search, filter, owner or follow-up columns, no lead page, no "add lead" on the page |
| Create lead | `POST /leads` | Working | Name, phone, service, source, value; ad attribution; emits `lead.created` for the WhatsApp agent |
| Edit / move / assign lead | `PATCH /leads/{id}` | Working | Status, notes, service, assignee; checks the assignee belongs to the business; audited |
| Pipeline (Kanban) | `VIEWS.pipeline` | Working | Leads by status; drag or menu; server confirms, card goes back on failure. No stage totals |
| Stage names per business type | `stageName()`, business_categories | Working | A restaurant's "booked" is a reservation |
| Customers | `/business/customers` (app/business.py), `VIEWS.customers` | Working | Add, search (in the browser), source filter; unique phone per business |
| Customer history | `GET /customers/{id}/history` (app/booking.py) | Working | Appointments, bills, payments, balance |
| Appointments / calendar | app/booking.py, app/operations.py | Working | Linked to customers |
| WhatsApp conversations | `conversations` table, Chats module | Working | Linked to leads by `lead_id` |
| AI follow-up | `leads.followup_at`, worker (`persistence.due_followups`) | Working | **The AI agent's own follow-up**: the worker messages the lead when it is due and clears the field. Not a human task list |
| Sales (TechDeva's own sales team) | app/sales.py, `VIEWS.sales` | Working | A separate module for TechDeva's sales operators; not the businesses' CRM. Left as it is |
| Roles | app/roles.py | Working | CRM is one section; view, create, edit, delete, approve per role |
| Tenant isolation | forced row-level security on leads, customers, conversations, appointments | Working | Foreign keys are checked past RLS, so every link must also be checked in the API (the lead assignee already is) |

### Missing

- **Human follow-ups and activities**: no tasks, calls, meetings or notes linked to a lead or customer; no My tasks / Today / Overdue.
- **A lead record page**: no single view of a lead's details, changes, conversations, appointments and follow-ups.
- **Server-side search, filters and pagination** for leads (the browser gets 200 rows).
- **Lead to customer conversion** with duplicate protection: today a customer is typed in again by hand.
- **Duplicate detection** (same phone written differently, same email).
- **Pipeline value**: no stage totals; no won / lost reasons.
- **CRM figures**: no lead source, conversion, overdue follow-up or pipeline numbers.
- **CRM automation** for people (inactivity reminders, assignment rules).
- **Jarvis**: reads leads and customers, cannot list or create follow-ups.

### Not needed, and why

- **Companies / accounts**: the businesses on TechDeva (clinics, salons, restaurants, gyms, academies, shops) sell to people. A company field on the lead covers the occasional business customer without forcing accounts on everyone.
- **A separate deals table**: a lead already moves through stages with a value (`est_revenue`) and a won stage (`sold`). A second deals table would duplicate it. The pipeline gets values, totals and won / lost reasons on the lead itself.
- **Lead scoring**: not enough history to validate a score. A made-up number would mislead; the CRM shows plain "needs attention" reasons instead.
- **A CRM inbox**: WhatsApp stays in Chats. The lead page links to the conversation.

### Data found (test copy)

35 leads, all unassigned; 13 with a phone number but only 3 distinct numbers (likely duplicates from tests). No automatic merge: duplicates are shown for review only.

### Rule for every phase

`leads.followup_at` stays the AI agent's. Human follow-ups live in their own table, so a reminder for a person never makes the AI message a customer.

## Phase B: core backend (done)

- **db/77_crm.sql** (additive): new optional lead fields (company, last contacted, lost reason, converted customer, status changed); `crm_activities` for people's follow-ups, calls, meetings, visits and notes; `crm_settings` for Phase E rules. Forced row-level security, plus a database check that an activity's lead, customer and person are in the same business. Existing leads proven unchanged (checksum before and after).
- **app/crm.py**:
  - `GET /crm/leads`: search (name, phone in any spelling, email, company, service), filters (status, source, owner, follow-up overdue/today/none, created dates), sorting, pages, counts by status.
  - `GET /crm/leads/{id}`: the lead with follow-ups and notes, its change history, WhatsApp conversations, appointments, the customer it became, and possible duplicates.
  - `POST /crm/leads/{id}/convert`: one transaction; reuses the matching customer; asks when two match; a second press changes nothing; history moves with the lead.
  - `/crm/activities`: views mine, all, today, upcoming, overdue (stays until done or moved), done; add, complete, move, reassign; remove only by the owner or the writer. A finished call, visit or message updates "last contacted".
  - `/crm/duplicates` and `/crm/duplicates/check`: for review only, never merged.
  - `/crm/team`: who a lead or follow-up can go to.
- **app/main.py**: `POST /leads` also takes email and company; `PATCH /leads` also takes name, phone, email, company, value, priority, lost reason, each validated; unchanged numbers are no longer recorded as changes; audit values that were dropped (a Decimal could not be written) are now recorded.
- **Tests**: tests/test_crm.py, 8 tests. Full suite 130 passed; the same 11 storefront set-up errors as before.

## Phase C: screens (done)

- **Leads** (same tab, same place): search as you type (name, any spelling of the phone, email, company), stage, source, owner and follow-up filters, sorting, 25 a page, saved views (All, My leads, New, Overdue follow-ups, No follow-up planned, Qualified), bulk assign and bulk move, and **Add lead** that warns when the person is already a lead or customer. Falls back to the old list if the CRM API is not on the server yet.
- **A lead's page** (`#/leads?id=`): stage and owner, Make customer (asks which one when two customers match), plan a follow-up or log a call, WhatsApp chat, visit, meeting or note, planned follow-ups with Done and Tomorrow, the full history (follow-ups, changes, WhatsApp conversations, bookings), editable details, and the WhatsApp agent's own follow-up shown read-only.
- **Follow-ups**: a new tab at the end of CRM (Mine, Due today, Overdue, Upcoming, All open, Done).
- **Customers**: each customer's page also shows planned follow-ups and the history of calls, visits and notes.
- **Pipeline, Appointments, Calendar, Patients/Customers lists**: unchanged.
- Checked in a browser at 1440px dark and light and 390px: the whole journey (add, plan, log, move, edit, search, saved view, done, convert, customer page), no sideways scroll, axe WCAG 2.2 AA clean, 44px targets, no script errors. Test data removed; leads proven unchanged.
