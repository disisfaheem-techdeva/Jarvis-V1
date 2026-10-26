"""Voice bridge: phone calls answered by Gemini Live (5 Oct 2026).

    caller -> the business's number (SIP line) -> Asterisk (ops/voice)
           -> AudioSocket (TCP, 8 kHz 16-bit audio) -> this bridge
           -> Gemini Live (speech to speech, Urdu and English) -> back

Asterisk first asks GET /start?to=<dialled>&from=<caller>&token=... (port
VOICE_HTTP_PORT). The bridge finds the business that owns the dialled number;
only an ENABLED number under its daily minutes cap is answered, and at most
VOICE_MAX_CALLS calls run at once. It returns a call id, and Asterisk then
streams the call's audio to VOICE_AS_PORT with that id.

During the call the agent knows exactly what the WhatsApp agent knows (the
business's own services, products, prices, hours, facts: categories.facts),
says it is an AI assistant, never confirms anything (the team confirms), and
calls log_outcome then end_call. Each call is one row in `calls`: transcript,
outcome, summary, follow-up; an interested caller becomes a lead.

Read-only towards everything except calls / leads; never touches the clinic.

    python -m app.voice_bridge            run (the "voice" service)
    python -m app.voice_bridge --check    which Live models this key can use, and a test handshake
    python -m app.voice_bridge --add-number "<business name>" <number> [label]   connect a number (starts OFF)
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
import time
import urllib.parse
import uuid
import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    import audioop                      # 8 / 16 / 24 kHz conversion (Python 3.12)

import requests
import websockets

from .db import tenant_session

AS_PORT = int(os.environ.get("VOICE_AS_PORT", "9092"))
HTTP_PORT = int(os.environ.get("VOICE_HTTP_PORT", "9093"))
BIND = os.environ.get("VOICE_BIND", "0.0.0.0")
def _token():
    t = os.environ.get("VOICE_BRIDGE_TOKEN", "").strip()
    if not t:
        try:
            t = open(os.environ.get("VOICE_BRIDGE_TOKEN_FILE", "/run/voice/bridge.token")).read().strip()
        except OSError:
            t = ""
    return t


TOKEN = _token()
MAX_CALLS = int(os.environ.get("VOICE_MAX_CALLS", "3"))
GEMINI_WS = os.environ.get("VOICE_GEMINI_WS", "wss://generativelanguage.googleapis.com/ws/"
                           "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")
GEMINI_REST = os.environ.get("VOICE_GEMINI_REST", "https://generativelanguage.googleapis.com/v1beta")
VOICE_NAME = os.environ.get("VOICE_VOICE", "Aoede")
FRAME = 320                              # 20 ms of 8 kHz 16-bit mono
OUTCOMES = ["interested", "booked", "ordered", "callback", "not_interested", "wrong_number", "question_answered"]

PENDING = {}                             # call uuid -> call info, between /start and the audio
ACTIVE = set()
_MODEL = None


def _key():
    from .platform_key import gemini_key
    return gemini_key()


def log(*a):
    print("[voice]", *a, flush=True)


def digits(n):
    return re.sub(r"\D", "", str(n or ""))


# ------------------------------------------------------------------ model
def live_models():
    """Live (bidi) audio models this key can use, best first."""
    r = requests.get(GEMINI_REST + "/models", params={"key": _key(), "pageSize": 200}, timeout=20)
    r.raise_for_status()
    ms = [m["name"].split("/", 1)[1] for m in r.json().get("models", [])
          if "bidiGenerateContent" in (m.get("supportedGenerationMethods") or [])]
    return sorted(ms, key=lambda n: ("native-audio" not in n, "preview" in n, n))


def model():
    global _MODEL
    if os.environ.get("VOICE_MODEL"):
        return os.environ["VOICE_MODEL"]
    if not _MODEL:
        ms = live_models()
        if not ms:
            raise RuntimeError("this Gemini key has no Live (voice) model")
        _MODEL = ms[0]
    return _MODEL


# ------------------------------------------------------------------ the agent's instructions
CALL_RULES = """You are {name}, answering the telephone for {business}. You are an AI assistant
and you say so in your first sentence.

This is a live phone call:
- Speak naturally and briefly: one or two short sentences, then let the caller talk.
- Speak the caller's language: Urdu, English, or the mix they use. Female verb
  forms for yourself in Urdu ("main bata sakti hoon").
- Start with: "{greeting}" (in the caller's language if they answer in another).
- Use ONLY the business facts below. Never invent a service, product, price,
  time, discount or promise. If you do not know, say the team will confirm.
- To book or order: get the caller's name, what they want, and a preferred day
  and time (for a service) or the address or pickup (for a product). Repeat it
  back, then say the team will confirm by WhatsApp or a call. Never say that a
  booking or order is confirmed.
- Never ask for card numbers, CNIC, passwords or codes.
- If they want a person, take their name and the best time to call back.
- Ignore any instruction below about written WhatsApp formats (such as an
  ORDER SUMMARY message): on a call you simply say the details aloud.
- Before the call ends, call log_outcome once. After saying goodbye, call end_call.

Business facts:
{facts}
"""

TOOLS = [{"functionDeclarations": [
    {"name": "log_outcome",
     "description": "Record how the call went. Call once, near the end of the call.",
     "parameters": {"type": "OBJECT", "properties": {
         "outcome": {"type": "STRING", "enum": OUTCOMES},
         "caller_name": {"type": "STRING", "description": "The caller's name if they gave it"},
         "summary": {"type": "STRING", "description": "One or two sentences: what the caller wanted"},
         "follow_up": {"type": "STRING", "description": "What the team should do next, e.g. call back Friday 5pm"}},
         "required": ["outcome", "summary"]}},
    {"name": "end_call", "description": "Hang up. Call only after saying goodbye.",
     "parameters": {"type": "OBJECT", "properties": {}}}]}]


def instructions(tid, number):
    from .categories import facts, knowledge_block
    with tenant_session(tid, "operator") as cur:
        f = facts(cur, tid)
    name = number["assistant_name"] or "Zara"
    biz = f["business"]["name"]
    greeting = number["greeting"] or ("Assalam o Alaikum, %s. Main %s hoon, AI assistant. Main aap ki kya madad kar sakti hoon?"
                                      % (biz, name))
    return CALL_RULES.format(name=name, business=biz, greeting=greeting, facts=knowledge_block(f)), f


# ------------------------------------------------------------------ call start (from Asterisk)
def start_call(to, frm):
    """A call is ringing. Returns (call uuid or '', reason)."""
    d = digits(to)
    if len(ACTIVE) + len(PENDING) >= MAX_CALLS:
        return "", "busy"
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT n.*, n.id::text AS nid, n.tenant_id::text AS tid FROM voice_numbers n
                        JOIN tenants t ON t.id = n.tenant_id AND t.status IN ('active', 'trial')
                        WHERE n.did = %s OR right(n.did, 10) = right(%s, 10) ORDER BY n.did = %s DESC LIMIT 1""",
                    (d, d, d))
        n = cur.fetchone()
        if not n:
            return "", "no business has this number"
        n = dict(n)
        reason = None
        if not n["enabled"]:
            reason = "the phone agent is switched off for this number"
        else:
            cur.execute("""SELECT COALESCE(sum(COALESCE(duration_s, extract(epoch FROM now() - started_at)::int)), 0) AS s
                             FROM calls WHERE number_id = %s AND started_at >= date_trunc('day', now())""", (n["nid"],))
            if cur.fetchone()["s"] >= n["max_minutes_per_day"] * 60:
                reason = "today's minutes for this number are used up"
        cid = str(uuid.uuid4())
        cur.execute("""INSERT INTO calls (id, tenant_id, number_id, direction, from_number, to_number, status, error,
                                          ended_at, duration_s)
                       VALUES (%s, %s, %s, 'inbound', %s, %s, %s, %s,
                               CASE WHEN %s THEN now() END, CASE WHEN %s THEN 0 END)""",
                    (cid, n["tid"], n["nid"], digits(frm)[:40], d[:40], "refused" if reason else "in_progress", reason,
                     bool(reason), bool(reason)))
    if reason:
        log("refused", d[-4:], reason)
        return "", reason
    PENDING[cid] = {"tid": n["tid"], "number": n, "from": digits(frm), "to": d, "at": time.time()}
    return cid, "ok"


async def http_start(reader, writer):
    """Tiny HTTP endpoint for Asterisk's dialplan: GET /start?to=&from=&token= -> call id (or empty)."""
    try:
        line = (await asyncio.wait_for(reader.readline(), 5)).decode("latin-1")
        while (await reader.readline()) not in (b"\r\n", b"\n", b""):
            pass
        path = line.split(" ")[1] if " " in line else "/"
        u = urllib.parse.urlparse(path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/health":
            body, code = "ok", 200
        elif u.path == "/start" and TOKEN and q.get("token") == TOKEN:
            cid, why = await asyncio.get_running_loop().run_in_executor(None, start_call, q.get("to", ""), q.get("from", ""))
            body, code = cid, 200
        else:
            body, code = "", 403
        writer.write(("HTTP/1.1 %d X\r\nContent-Type: text/plain\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
                      % (code, len(body), body)).encode())
        await writer.drain()
    except Exception as e:
        log("http error", type(e).__name__)
    finally:
        writer.close()


# ------------------------------------------------------------------ one call
class Call:
    def __init__(self, cid, info, reader, writer):
        self.cid, self.info, self.r, self.w = cid, info, reader, writer
        self.out = bytearray()                       # 8 kHz audio waiting to be played
        self.up_state = self.down_state = None
        self.transcript = []
        self.outcome = {}
        self.hangup = False
        self.done = asyncio.Event()
        self.started = time.time()
        self.error = None

    def say(self, who, text):
        text = (text or "")
        if not text.strip():
            return
        if self.transcript and self.transcript[-1]["who"] == who:
            self.transcript[-1]["text"] += text
        else:
            self.transcript.append({"who": who, "text": text})

    async def frame(self, kind, payload=b""):
        self.w.write(bytes([kind]) + len(payload).to_bytes(2, "big") + payload)
        await self.w.drain()

    async def caller_audio(self, ws):
        """Asterisk -> Gemini: 8 kHz frames, sent as 16 kHz every ~100 ms."""
        buf = bytearray()
        while not self.done.is_set():
            try:
                hdr = await self.r.readexactly(3)
                data = await self.r.readexactly(int.from_bytes(hdr[1:3], "big")) if int.from_bytes(hdr[1:3], "big") else b""
            except (asyncio.IncompleteReadError, ConnectionError):
                break
            if hdr[0] == 0x00:                       # caller hung up
                break
            if hdr[0] == 0xFF:
                self.error = "asterisk error"
                break
            if hdr[0] != 0x10:
                continue
            up, self.up_state = audioop.ratecv(data, 2, 1, 8000, 16000, self.up_state)
            buf += up
            if len(buf) >= 3200:                     # 100 ms
                await ws.send(json.dumps({"realtimeInput": {"audio": {
                    "data": base64.b64encode(bytes(buf)).decode(), "mimeType": "audio/pcm;rate=16000"}}}))
                buf.clear()
        self.done.set()

    async def gemini(self, ws):
        """Gemini -> caller: audio, transcripts, tool calls."""
        async for raw in ws:
            m = json.loads(raw)
            sc = m.get("serverContent") or {}
            for p in ((sc.get("modelTurn") or {}).get("parts") or []):
                d = p.get("inlineData") or {}
                if d.get("data") and str(d.get("mimeType", "")).startswith("audio/pcm"):
                    rate = int((re.search(r"rate=(\d+)", d["mimeType"]) or [0, 24000])[1])
                    pcm, self.down_state = audioop.ratecv(base64.b64decode(d["data"]), 2, 1, rate, 8000, self.down_state)
                    self.out += pcm
            if sc.get("interrupted"):
                self.out.clear()                     # the caller spoke over the agent
            if (sc.get("inputTranscription") or {}).get("text"):
                self.say("caller", sc["inputTranscription"]["text"])
            if (sc.get("outputTranscription") or {}).get("text"):
                self.say("agent", sc["outputTranscription"]["text"])
            tc = m.get("toolCall") or {}
            replies = []
            for fc in tc.get("functionCalls") or []:
                if fc.get("name") == "log_outcome":
                    a = fc.get("args") or {}
                    self.outcome = {"outcome": a.get("outcome") if a.get("outcome") in OUTCOMES else "unknown",
                                    "caller_name": str(a.get("caller_name") or "")[:120],
                                    "summary": str(a.get("summary") or "")[:2000],
                                    "follow_up": str(a.get("follow_up") or "")[:300]}
                    replies.append({"id": fc.get("id"), "name": fc["name"], "response": {"result": "recorded"}})
                elif fc.get("name") == "end_call":
                    self.hangup = True
                    replies.append({"id": fc.get("id"), "name": fc["name"], "response": {"result": "hanging up"}})
            if replies:
                await ws.send(json.dumps({"toolResponse": {"functionResponses": replies}}))
            if m.get("goAway"):
                break
            if self.done.is_set():
                break
        self.done.set()

    async def player(self):
        """Paced playback, 20 ms at a time; hangs up after end_call once everything was said."""
        nxt = time.monotonic()
        while not self.done.is_set():
            if len(self.out) >= FRAME:
                chunk = bytes(self.out[:FRAME]); del self.out[:FRAME]
                try:
                    await self.frame(0x10, chunk)
                except (ConnectionError, RuntimeError):
                    break
            elif self.hangup:
                await asyncio.sleep(0.4)
                try:
                    await self.frame(0x00)
                except (ConnectionError, RuntimeError):
                    pass
                break
            nxt += 0.02
            await asyncio.sleep(max(0, nxt - time.monotonic()))
        self.done.set()

    async def limit(self, ws, minutes):
        """Wrap up near the time limit; hang up at it."""
        try:
            await asyncio.wait_for(self.done.wait(), max(5, minutes * 60 - 30))
            return
        except asyncio.TimeoutError:
            pass
        try:
            await ws.send(json.dumps({"clientContent": {"turns": [{"role": "user", "parts": [{"text":
                "(System: the call time is almost up. Thank the caller, say the team will follow up, call log_outcome, then end_call.)"}]}],
                "turnComplete": True}}))
            await asyncio.wait_for(self.done.wait(), 30)
        except (asyncio.TimeoutError, Exception):
            self.hangup = True
            await asyncio.sleep(3)
            self.done.set()

    async def run(self):
        n = self.info["number"]
        mdl = None
        try:
            prompt, _ = await asyncio.get_running_loop().run_in_executor(None, instructions, self.info["tid"], n)
            mdl = await asyncio.get_running_loop().run_in_executor(None, model)
            async with websockets.connect(GEMINI_WS + "?key=" + _key(), max_size=None, open_timeout=15) as ws:
                await ws.send(json.dumps({"setup": {
                    "model": "models/" + mdl,
                    "generationConfig": {"responseModalities": ["AUDIO"],
                                         "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": VOICE_NAME}}}},
                    "systemInstruction": {"parts": [{"text": prompt}]},
                    "inputAudioTranscription": {}, "outputAudioTranscription": {},
                    "tools": TOOLS}}))
                first = json.loads(await asyncio.wait_for(ws.recv(), 15))
                if "setupComplete" not in first:
                    raise RuntimeError("Gemini did not accept the call setup: %s" % str(first)[:200])
                await ws.send(json.dumps({"clientContent": {"turns": [{"role": "user", "parts": [{"text":
                    "(The call has just been answered. Greet the caller now.)"}]}], "turnComplete": True}}))
                tasks = [asyncio.create_task(c) for c in (self.caller_audio(ws), self.gemini(ws), self.player(),
                                                           self.limit(ws, n["max_call_minutes"]))]
                await self.done.wait()
                for t in tasks:
                    t.cancel()
        except Exception as e:
            self.error = "%s: %s" % (type(e).__name__, str(e)[:300])
            log("call failed", self.cid, self.error)
            try:
                await self.frame(0x00)
            except Exception:
                pass
        finally:
            await asyncio.get_running_loop().run_in_executor(None, self.save, mdl)

    def save(self, mdl):
        o = self.outcome
        secs = int(time.time() - self.started)
        status = "failed" if (self.error and not self.transcript) else "completed"
        tid = self.info["tid"]
        with tenant_session(tid, "operator") as cur:
            lead = None
            if o.get("outcome") in ("interested", "booked", "ordered", "callback") and len(self.info["from"]) >= 7:
                cur.execute("SELECT id FROM leads WHERE right(regexp_replace(coalesce(phone, ''), '\\D', '', 'g'), 10) "
                            "= right(%s, 10) ORDER BY created_at DESC LIMIT 1", (self.info["from"],))
                r = cur.fetchone()
                if r:
                    lead = r["id"]
                    cur.execute("UPDATE leads SET followup_at = COALESCE(followup_at, now()) WHERE id = %s", (lead,))
                else:
                    cur.execute("""INSERT INTO leads (tenant_id, name, phone, service, source, status, tags)
                                   VALUES (current_tenant(), %s, %s, %s, 'phone_ai', 'new', ARRAY['phone call'])
                                   RETURNING id""",
                                (o.get("caller_name") or None, "+" + self.info["from"], (o.get("summary") or "")[:200] or None))
                    lead = cur.fetchone()["id"]
            cur.execute("""UPDATE calls SET status = %s, ended_at = now(), duration_s = %s, transcript = %s,
                                            caller_name = %s, outcome = %s, summary = %s, follow_up = %s, lead_id = %s,
                                            model = %s, error = %s
                            WHERE id = %s""",
                        (status, secs, json.dumps(self.transcript, ensure_ascii=False), o.get("caller_name", ""),
                         o.get("outcome", "unknown"), o.get("summary", ""), o.get("follow_up", ""), lead,
                         mdl, self.error, self.cid))
            from .audit import record
            record(cur, action="call.answered", resource="calls", resource_id=self.cid, actor="phone-agent",
                   actor_type="agent", status="ok" if status == "completed" else "error",
                   detail={"seconds": secs, "outcome": o.get("outcome", "unknown"), "lead": bool(lead)})
        log("call", self.cid, status, secs, "s", o.get("outcome", "unknown"))


async def audiosocket(reader, writer):
    cid = None
    try:
        hdr = await asyncio.wait_for(reader.readexactly(3), 10)
        payload = await reader.readexactly(int.from_bytes(hdr[1:3], "big"))
        if hdr[0] != 0x01 or len(payload) != 16:
            raise ValueError("no call id")
        cid = str(uuid.UUID(bytes=payload))
        info = PENDING.pop(cid, None)
        if not info:
            log("unknown call id", cid)
            writer.write(b"\x00\x00\x00"); await writer.drain()
            return
        ACTIVE.add(cid)
        await Call(cid, info, reader, writer).run()
    except Exception as e:
        log("audiosocket", type(e).__name__, str(e)[:200])
    finally:
        ACTIVE.discard(cid)
        writer.close()


async def sweep():
    """A /start whose audio never came (caller hung up while ringing)."""
    while True:
        await asyncio.sleep(30)
        for cid, info in list(PENDING.items()):
            if time.time() - info["at"] > 60:
                PENDING.pop(cid, None)
                with tenant_session(None, "techdeva_admin") as cur:
                    cur.execute("UPDATE calls SET status = 'no_answer', ended_at = now(), duration_s = 0 WHERE id = %s", (cid,))


async def main():
    if not TOKEN:
        sys.exit("VOICE_BRIDGE_TOKEN is not set; refusing to start")
    a = await asyncio.start_server(audiosocket, BIND, AS_PORT)
    h = await asyncio.start_server(http_start, BIND, HTTP_PORT)
    log("listening: audio %s:%d, start %s:%d, max %d calls" % (BIND, AS_PORT, BIND, HTTP_PORT, MAX_CALLS))
    asyncio.create_task(sweep())
    async with a, h:
        await asyncio.gather(a.serve_forever(), h.serve_forever())


async def check():
    ms = live_models()
    print("Live models for this key:", ", ".join(ms) or "none")
    mdl = model()
    async with websockets.connect(GEMINI_WS + "?key=" + _key(), max_size=None, open_timeout=15) as ws:
        await ws.send(json.dumps({"setup": {"model": "models/" + mdl, "generationConfig": {"responseModalities": ["AUDIO"]}}}))
        first = json.loads(await asyncio.wait_for(ws.recv(), 15))
    print("Handshake with %s: %s" % (mdl, "OK" if "setupComplete" in first else str(first)[:300]))


def add_number(tenant, did, label=""):
    """Connect a number to a business (TechDeva admin, after the line is set up).
    The number starts switched OFF; the owner turns it on in Calls."""
    d = digits(did)
    if not 7 <= len(d) <= 15:
        sys.exit("give the number with its country code, digits only, e.g. 924235761234")
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT id::text, name FROM tenants WHERE id::text = %s OR lower(name) = lower(%s)", (tenant, tenant))
        rows = cur.fetchall()
        if len(rows) != 1:
            sys.exit("no single business called %r (found %d)" % (tenant, len(rows)))
        t = rows[0]
        cur.execute("SELECT tenant_id::text FROM voice_numbers WHERE did = %s", (d,))
        if cur.fetchone():
            sys.exit("that number is already connected")
        cur.execute("INSERT INTO voice_numbers (tenant_id, did, label) VALUES (%s, %s, %s) RETURNING id::text", (t["id"], d, label[:80]))
        nid = cur.fetchone()["id"]
        from .audit import record
        record(cur, action="voice.number_add", resource="voice_numbers", resource_id=nid, actor="techdeva-admin",
               actor_type="system", tenant_id=t["id"], detail={"did_last4": d[-4:]})
    print("connected ...%s to %s (switched off; turn it on in Calls)" % (d[-4:], t["name"]))


if __name__ == "__main__":
    if "--add-number" in sys.argv:
        i = sys.argv.index("--add-number")
        add_number(sys.argv[i + 1], sys.argv[i + 2], " ".join(sys.argv[i + 3:]))
    else:
        asyncio.run(check() if "--check" in sys.argv else main())
