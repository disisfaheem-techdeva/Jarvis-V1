# Jarvis API v1

The versioned contract for anything that talks to Jarvis: the dashboard, a till, a
phone app, WhatsApp. Code: `app/jarvis_api.py`. Decision: ADR-003 (TechDeva docs repo).
Tests: `tests/test_jarvis_api.py`.

It is a facade over the Jarvis that already runs at `/console`, not a second Jarvis.
The same route functions answer, so sign-in, row-level security, roles, trial locks,
AI credits, audit and the per-person history are exactly the dashboard's. No model
provider is named or reachable through this API; providers stay behind `app/ai.py`.

`/console/...` is unchanged. Breaking changes go to `/jarvis/v2`, never into v1.

## Sign-in

Every call needs the platform's own sign-in: the session cookie or
`Authorization: Bearer <token>`. A founder acting for one business passes
`tenant_id` in the body. Jarvis can never do more than the signed-in person could
do by hand.

## Routes

| Method | Path | Does |
|---|---|---|
| GET | `/jarvis/v1` | Capabilities: kinds, actions, which need a yes, languages, limits |
| POST | `/jarvis/v1/messages` | One thing the person said or typed |
| POST | `/jarvis/v1/confirmations` | The person's "yes" to a confirmation |
| POST | `/jarvis/v1/speech` | Jarvis's voice for a reply (`audio/wav`) |
| GET | `/jarvis/v1/conversations` | This person's conversations, newest first (`?limit=`, max 100) |
| GET | `/jarvis/v1/conversations/{id}` | One conversation with its messages |

## POST /jarvis/v1/messages

```json
{ "text": "4200 bijli ka bill likh do",
  "conversation_id": "optional, to continue one",
  "voice": false,
  "image_urls": ["optional, links this platform issued"] }
```

`text` is required, at most 2,000 characters.

## Response (messages and confirmations)

```json
{ "api": "jarvis/v1",
  "conversation_id": "…",
  "kind": "answer | confirm | done",
  "intent": "question | confirm | action | teach | dashboard | post | business",
  "reply": "what to show or say",
  "confirmation": { "token": "…", "action": "add_expense",
                    "summary": "Record an expense of PKR 4,200 for bijli bill?",
                    "expires_in": 300 },
  "action": { "name": "add_expense", "ok": true },
  "navigate": "expenses",
  "data": { "anything else the action returned" } }
```

- `kind` is what a client must handle: show the `answer`, ask the `confirm`
  question, or show that it is `done`. `intent` is Jarvis's own finer label.
- `confirmation` appears only on `confirm`; `action` only when something was done.

## POST /jarvis/v1/confirmations

```json
{ "token": "<confirmation.token>", "said": "Haan", "conversation_id": "…" }
```

Runs exactly what was sealed, once, with no new model call. The token is bound to
the same person, business and role and expires after 5 minutes. A second use, even
after the API restarts, gets 409 "That is already done." (used tokens are held in
Redis; if Redis is unreachable, the API process's own list is used, as before v1).

## Errors

Every error has one shape and the HTTP status of the problem:

```json
{ "api": "jarvis/v1", "error": { "status": 409, "message": "That is already done." },
  "conversation_id": "present when the turn was kept" }
```

| Status | Means |
|---|---|
| 400 | Missing or invalid input, or an invalid confirmation token |
| 401 | Not signed in |
| 402 | This month's AI credits are used up |
| 403 | That confirmation belongs to someone else, or the role may not do this |
| 404 | That conversation is not yours, or was deleted |
| 409 | That confirmation was already used |
| 410 | The confirmation expired |
| 503 | The AI or the voice could not be reached; nothing was changed |

## Not in v1 yet

Speech-to-text on the server (clients send text; the dashboard uses the browser's),
POS, stock and udhaar actions (phases 4 to 6 of the Jarvis plan).
