# AI phone agent (step 1: incoming calls)

Calls to a business's own number are answered by an AI assistant that knows
exactly what that business's WhatsApp agent knows (its services, products,
prices, hours, facts). It says it is an AI assistant, never confirms a booking
or order (the team does), logs an outcome, and hangs up after goodbye. Each
call appears in the dashboard under **Calls** with the transcript, outcome and
next step; an interested caller becomes a lead.

```
caller -> business number (SIP line, PTA-licensed provider)
       -> Asterisk  (ops/voice, its own container, host network)
       -> voice bridge (app/voice_bridge.py, compose service "voice", OFF by default)
       -> Gemini Live (speech to speech, Urdu + English; GEMINI_API_KEY)
```

Nothing here touches the clinic CRM or the Hermes / WhatsApp containers.

## What answers, and what does not

- Only a number connected to a business AND switched on by its owner (Calls
  page) is answered. Anything else gets a busy tone and is never picked up.
- Per number: longest call (default 8 min; the agent wraps up 30 s before) and
  minutes per day (default 60; the budget cap). At most `VOICE_MAX_CALLS`
  (default 3) calls at once.
- Connecting a number is TechDeva's job (it needs the line on the server); the
  owner only switches it on/off and sets the name, greeting and limits.

## Setting it up on the server (once)

All as root, in `/opt/techdeva-platform/techdeva-platform`. Secrets are typed
by Faheem into files on the server, never into chat or the repo.

1. Check the Gemini key has a Live voice model:
   `docker compose run --rm --no-deps api python -m app.voice_bridge --check`
   It lists the Live models and says `Handshake ... OK`. If it fails, nothing
   else below will work: fix the key/plan first.
2. Secrets folder: `bash ops/voice/setup.sh` creates `/etc/techdeva/voice`
   (root-only) with a random `bridge.token` and `trunk.conf` from the example.
3. Faheem types the line's details himself: `nano /etc/techdeva/voice/trunk.conf`
   (SIP server, username, password from the provider's email; replace every
   CHANGE_ME).
4. Check ports 5060/udp and 10000-10100/udp are free on the server
   (`ss -lunp | grep -E ':5060|:100[0-9][0-9]'` shows nothing).
5. Firewall: allow those ports ONLY from the provider's address(es), e.g.
   `ufw allow from <provider-ip> to any port 5060 proto udp` and
   `ufw allow from <provider-ip> to any port 10000:10100 proto udp`.
   (Ask the provider for their signalling and media IPs.)
6. Start the bridge: `docker compose --profile voice up -d --build voice`
7. Start the phone exchange: `cd ops/voice && docker compose up -d --build`
   Check: `docker exec techdeva-voice-asterisk asterisk -rx "pjsip show registrations"`
   shows the line as Registered.
8. Connect the number to the business (digits with country code):
   `docker compose exec api python -m app.voice_bridge --add-number "TechDeva" 924235761234 "Office line"`
   It starts switched OFF.
9. The owner switches it on in **Calls**, then calls the number from a mobile.

## Turning it off

- One number: the owner presses **Switch off** in Calls (callers hear busy).
- Everything: `cd ops/voice && docker compose stop` and
  `docker compose --profile voice stop voice`.

## Not in step 1 (next)

Outgoing calls to approved warm leads (owner approves the list, calling hours,
attempt limits, Do Not Call), call recordings, and Jarvis starting calls.
