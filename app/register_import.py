"""Bring the old register (Phase 7, 9 Nov 2026; db/96, ADR-004).

    "Bring your old register. Jarvis will digitise your business."

1. The owner creates an import and uploads photos (or a PDF) of the register's
   pages. Pages are kept privately in ledger_import_pages, never in the public
   post media store.
2. Each page is read, one request per page, by a model that can see pictures
   (the platform's chain, app/ai.py). It returns rows (name, phone, amount, date,
   kind, the text as written) with a confidence for each. Nothing is guessed in:
   a value the model is unsure of is left empty or given a low confidence.
3. Every row gets flags. BLOCKING (cannot be approved until fixed): no name, no
   amount, a name that matches more than one customer with no phone. CHECK (the
   owner approves that row on its own, never in bulk): low confidence, possible
   duplicate, no date on a transaction. Informational: a new customer.
4. The owner corrects and approves or rejects rows, or approves every row with no
   flag at once.
5. Import writes each approved row through the udhaar ledger's own add_entry
   (app/ledger.py, source 'import', the row's id as its reference), so a second
   Import never writes twice, a payment is never more than owed, and every entry
   points back to its page. An imported row is locked.

Accuracy is measured, not assumed: of the rows kept, how many the owner did not
have to change ("edited"), and how many were thrown away.
"""
from __future__ import annotations

import base64
import binascii
import datetime as _dt
import json
import os
import re
import time

import requests
from fastapi import APIRouter, Body, Cookie, Header, HTTPException, Response

from .audit import record
from .auth import SESSION_COOKIE
from .db import tenant_session
from . import ledger as L

MAX_PAGES, MAX_BYTES, MAX_ROWS = 60, 8_000_000, 120
LOW = 0.8
BLOCKING = {"no_name", "no_amount", "ambiguous_customer"}
CHECK = {"low_confidence", "possible_duplicate", "no_date"}
UUID = re.compile(r"^[0-9a-fA-F-]{36}$")
MIMES = {"data:image/jpeg;base64,": "image/jpeg", "data:image/png;base64,": "image/png",
         "data:application/pdf;base64,": "application/pdf"}
READ_TIMEOUT, READ_BUDGET = 60, 90

ASK = """This is a photo of a page from a Pakistani shop's handwritten udhaar (credit)
register. It may be in Urdu, Roman Urdu or English, in any handwriting.

%s

Answer with JSON only, no other words:
{"rows": [{"text": "the line exactly as written, transliterated if Urdu",
           "name": "the customer's name in Latin letters, or null",
           "phone": "the phone number if written, or null",
           "amount": the amount as a number in rupees, or null,
           "date": "YYYY-MM-DD if a date is written for this line, or null",
           "kind": "%s",
           "confidence": a number from 0 to 1: how sure you are of the name AND the amount}]}

Rules: one row per customer line. Never invent a name, phone, amount or date: if you
cannot read it, put null and a low confidence. Skip page headings, totals of the
whole page, and crossed-out lines. Do not add up anything yourself."""
MODES = {
    "balances": ("Each line is a customer and what they owe the shop now (their balance).", "opening"),
    "transactions": ("Each line is one transaction: goods given on credit to a customer (udhaar), "
                     "or money a customer paid back (jama / wusool / paid).", "credit or payment"),
}


class ImportError_(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


# ------------------------------------------------------------------ reading a page
def _vision_gemini(ai, key, model, b64, mime, ask):
    r = requests.post("%s/v1beta/models/%s:generateContent" % (ai.base_url("gemini"), model),
                      params={"key": key}, timeout=READ_TIMEOUT,
                      json={"contents": [{"parts": [{"inline_data": {"mime_type": mime, "data": b64}}, {"text": ask}]}],
                            "generationConfig": {"maxOutputTokens": 8000, "responseMimeType": "application/json"}})
    if r.status_code >= 400:
        return r.status_code, ""
    c = (r.json().get("candidates") or [{}])[0]
    return 200, "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts", []))


def _vision_openai(ai, key, model, b64, mime, ask):
    if mime == "application/pdf":
        return 415, ""
    r = requests.post(ai.base_url("openai") + "/v1/chat/completions", timeout=READ_TIMEOUT,
                      headers={"Authorization": "Bearer " + key},
                      json={"model": model, "max_tokens": 8000, "messages": [{"role": "user", "content": [
                          {"type": "text", "text": ask},
                          {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, b64)}}]}]})
    if r.status_code >= 400:
        return r.status_code, ""
    return 200, ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""


def _json(text):
    t = str(text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b < a:
        raise ValueError("no JSON")
    return json.loads(t[a:b + 1])


def read_page(data, mime, mode):
    """(rows, model) for one page. Raises RuntimeError with a plain reason."""
    from . import ai, usage
    from .setup_reader import _model_for
    why = usage.check()
    if why:
        raise RuntimeError(why)
    say, kind = MODES[mode]
    ask = ASK % (say, kind)
    b64 = base64.b64encode(data).decode()
    calls = {"gemini": _vision_gemini, "openai": _vision_openai}
    codes, started = [], time.time()
    for p, m in ai.chain_entries():
        key = os.environ.get(ai._KEY_ENV.get(p, ""), "").strip()
        if p not in calls or not key:
            continue
        if time.time() - started > READ_BUDGET:
            break
        model = _model_for(ai, p, m)
        try:
            code, text = calls[p](ai, key, model, b64, mime, ask)
            if code == 200:
                rows = _json(text).get("rows")
                if isinstance(rows, list):
                    usage.charge("register_read", True, provider=p, model=model, ms=(time.time() - started) * 1000,
                                 chars_out=len(str(text)))
                    return rows[:MAX_ROWS], "%s:%s" % (p, model)
                code = 0
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
            code = 0
        codes.append(code)
    if not codes:
        raise RuntimeError("Reading register pages needs an AI that can see pictures, and none is set up.")
    if 429 in codes:
        raise RuntimeError("Reading pictures is not available on the current AI plan right now. Try again later.")
    if 415 in codes and all(c in (415, 0) for c in codes):
        raise RuntimeError("This AI cannot read PDFs. Upload photos of the pages instead.")
    raise RuntimeError("The AI could not read this page. Try a sharper, straighter photo in good light.")


# ------------------------------------------------------------------ rows and flags
def _clean_row(x, mode):
    def num(v):
        try:
            n = round(float(str(v).replace(",", "").replace("Rs", "").strip()), 2)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None

    def day(v):
        try:
            d = _dt.date.fromisoformat(str(v)[:10])
            return d if d <= _dt.date.today() + _dt.timedelta(days=1) else None
        except (TypeError, ValueError):
            return None
    x = x if isinstance(x, dict) else {}
    kind = str(x.get("kind") or "").strip().lower()
    kind = "opening" if mode == "balances" else ("payment" if kind == "payment" else "credit")
    try:
        conf = max(0.0, min(1.0, float(x.get("confidence"))))
    except (TypeError, ValueError):
        conf = 0.0
    name = re.sub(r"\s+", " ", str(x.get("name") or "")).strip()[:120] or None
    phone = re.sub(r"[^\d+ ]", "", str(x.get("phone") or "")).strip()[:40] or None
    return {"text": str(x.get("text") or "").strip()[:500] or None, "name": name, "phone": phone,
            "amount": num(x.get("amount")), "date": day(x.get("date")), "kind": kind, "confidence": round(conf, 3)}


def flags(cur, r, mode):
    """The row's flags and its status, from what is settled now."""
    f = []
    if not r["name"]:
        f.append("no_name")
    if r["amount"] is None:
        f.append("no_amount")
    if not r["edited"] and (r["confidence"] is None or float(r["confidence"]) < LOW):
        f.append("low_confidence")
    if mode == "transactions" and not r["entry_date"]:
        f.append("no_date")
    match = None
    if r["customer_id"]:
        match = str(r["customer_id"])
    elif r["name"] or r["phone"]:
        found = L.find(cur, r["name"], r["phone"])
        d = L._digits(r["phone"])
        exact = [c for c in found if (len(d) >= 7) or c["name"].strip().lower() == (r["name"] or "").strip().lower()]
        if len(exact) == 1:
            match = exact[0]["customer_id"]
        elif len(exact) > 1:
            f.append("ambiguous_customer")
        else:
            f.append("new_customer")
    if r["amount"] is not None and r["name"]:
        cur.execute("""SELECT 1 FROM ledger_import_rows WHERE import_id = %s AND id <> %s AND status <> 'rejected'
                         AND lower(btrim(name)) = lower(btrim(%s)) AND amount = %s
                         AND entry_date IS NOT DISTINCT FROM %s LIMIT 1""",
                    (r["import_id"], r["id"], r["name"], r["amount"], r["entry_date"]))
        dup = cur.fetchone()
        if not dup and match:
            sign = -1 if r["kind"] == "payment" else 1
            cur.execute("""SELECT 1 FROM customer_ledger_entries WHERE customer_id = %s AND amount = %s
                             AND occurred_on = COALESCE(%s, occurred_on) AND kind = %s AND id IS DISTINCT FROM %s LIMIT 1""",
                        (match, sign * float(r["amount"]), r["entry_date"], r["kind"], r.get("ledger_entry_id")))
            dup = cur.fetchone()
        if dup:
            f.append("possible_duplicate")
    status = "needs_check" if (set(f) & (BLOCKING | CHECK)) else "ready"
    return f, status


def _refresh(cur, row_id, mode, keep_decision=False):
    cur.execute("SELECT * FROM ledger_import_rows WHERE id = %s", (row_id,))
    r = cur.fetchone()
    if r["status"] in ("imported",):
        return
    f, status = flags(cur, r, mode)
    if keep_decision and r["status"] in ("approved", "rejected"):
        status = r["status"]
    cur.execute("UPDATE ledger_import_rows SET flags = %s, status = %s WHERE id = %s", (f, status, row_id))


# ------------------------------------------------------------------ the import
def _imp(cur, iid):
    if not UUID.match(str(iid)):
        raise ImportError_(404, "That register import is not in this business.")
    cur.execute("SELECT * FROM ledger_imports WHERE id::text = %s", (str(iid),))
    i = cur.fetchone()
    if not i:
        raise ImportError_(404, "That register import is not in this business.")
    return i


def create(cur, body, actor):
    mode = str(body.get("mode") or "balances")
    if mode not in MODES:
        raise ImportError_(400, "Choose what the pages hold: balances (what each customer owes now) or transactions.")
    title = re.sub(r"\s+", " ", str(body.get("title") or "")).strip()[:120] or None
    cur.execute("INSERT INTO ledger_imports (tenant_id, mode, title, created_by) VALUES (current_tenant(), %s, %s, %s) "
                "RETURNING id::text", (mode, title, actor))
    iid = cur.fetchone()["id"]
    record(cur, action="register.import_start", resource="ledger_imports", resource_id=iid, actor=actor, detail={"mode": mode})
    return view(cur, iid)


def add_page(cur, iid, body, actor):
    i = _imp(cur, iid)
    if i["status"] != "open":
        raise ImportError_(409, "This import is closed. Start a new one for more pages.")
    data = str(body.get("image") or "")
    mime = next((m for p, m in MIMES.items() if data.startswith(p)), None)
    if not mime:
        raise ImportError_(400, "Send a photo (JPEG or PNG) or a PDF of the page.")
    try:
        raw = base64.b64decode(data.split(",", 1)[1], validate=True)
    except (ValueError, binascii.Error):
        raise ImportError_(400, "That file could not be read.")
    if not raw or len(raw) > MAX_BYTES:
        raise ImportError_(413, "A page must be smaller than 8 MB. Take the photo again at a lower size.")
    if (mime == "image/jpeg" and not raw.startswith(b"\xff\xd8")) or (mime == "image/png" and not raw.startswith(b"\x89PNG")) \
            or (mime == "application/pdf" and not raw.startswith(b"%PDF")):
        raise ImportError_(400, "That file is not the kind it says it is.")
    cur.execute("SELECT count(*) AS n, COALESCE(max(page_no), 0) AS m FROM ledger_import_pages WHERE import_id = %s", (i["id"],))
    c = cur.fetchone()
    if c["n"] >= MAX_PAGES:
        raise ImportError_(400, "One import takes up to %d pages. Start another for the rest." % MAX_PAGES)
    cur.execute("""INSERT INTO ledger_import_pages (tenant_id, import_id, page_no, mime, bytes, byte_size)
                   VALUES (current_tenant(), %s, %s, %s, %s, %s) RETURNING id::text, page_no""",
                (i["id"], c["m"] + 1, mime, raw, len(raw)))
    p = cur.fetchone()
    return {"page": {"id": p["id"], "page_no": p["page_no"], "status": "waiting"}}


def page_bytes(cur, iid, pid):
    _imp(cur, iid)
    if not UUID.match(str(pid)):
        raise ImportError_(404, "That page is not in this import.")
    cur.execute("SELECT mime, bytes FROM ledger_import_pages WHERE id::text = %s AND import_id::text = %s", (pid, str(iid)))
    p = cur.fetchone()
    if not p:
        raise ImportError_(404, "That page is not in this import.")
    return p["mime"], bytes(p["bytes"])


def read(tid, role, iid, pid, actor):
    """Read one page with the AI (outside a transaction: it can take a while), then store its rows."""
    with tenant_session(tid, role) as cur:
        i = _imp(cur, iid)
        if i["status"] != "open":
            raise ImportError_(409, "This import is closed.")
        mime, data = page_bytes(cur, iid, pid)
        cur.execute("SELECT status FROM ledger_import_pages WHERE id::text = %s", (pid,))
        if cur.fetchone()["status"] == "read":
            cur.execute("SELECT count(*) AS n FROM ledger_import_rows WHERE page_id::text = %s AND status = 'imported'", (pid,))
            if cur.fetchone()["n"]:
                raise ImportError_(409, "Rows from this page were already imported; it cannot be read again.")
    try:
        rows, model = read_page(data, mime, i["mode"])
        err = None
    except RuntimeError as e:
        rows, model, err = [], None, str(e)[:300]
    if err:
        # saved in its own transaction first, so the page shows "failed" even though we then raise
        with tenant_session(tid, role) as cur:
            cur.execute("UPDATE ledger_import_pages SET status = 'failed', error = %s, read_at = now() WHERE id::text = %s", (err, pid))
        raise ImportError_(503, err + " Nothing was added.")
    with tenant_session(tid, role) as cur:
        cur.execute("DELETE FROM ledger_import_rows WHERE page_id::text = %s AND status <> 'imported'", (pid,))
        cleaned = [_clean_row(x, i["mode"]) for x in rows]
        cleaned = [x for x in cleaned if x["name"] or x["amount"] is not None or x["text"]]
        for n, x in enumerate(cleaned, start=1):
            cur.execute("""INSERT INTO ledger_import_rows (tenant_id, import_id, page_id, row_no, read_text, read_name,
                                                           read_phone, read_amount, read_date, read_kind, confidence,
                                                           name, phone, amount, entry_date, kind)
                           VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           RETURNING id""",
                        (i["id"], pid, n, x["text"], x["name"], x["phone"], x["amount"], x["date"], x["kind"], x["confidence"],
                         x["name"], x["phone"], x["amount"], x["date"], x["kind"]))
        # flags only once every row is in: the first of two identical lines must see the second
        cur.execute("SELECT id FROM ledger_import_rows WHERE import_id = %s AND status IN ('needs_check','ready')", (i["id"],))
        for r in cur.fetchall():
            _refresh(cur, r["id"], i["mode"])
        cur.execute("""UPDATE ledger_import_pages SET status = 'read', error = NULL, model = %s, answer = %s, read_at = now()
                        WHERE id::text = %s""", (model, json.dumps({"rows": rows}, default=str)[:200000], pid))
        record(cur, action="register.page_read", resource="ledger_import_pages", resource_id=str(pid), actor=actor,
               detail={"rows": len(cleaned), "model": model})
        return view(cur, iid)


def edit_row(cur, iid, rid, body, actor):
    i = _imp(cur, iid)
    r = _row(cur, i, rid)
    if r["status"] == "imported":
        raise ImportError_(409, "That row is already in the ledger. Correct it there.")
    new = {k: r[k] for k in ("name", "phone", "amount", "entry_date", "kind", "customer_id")}
    if "name" in body:
        new["name"] = re.sub(r"\s+", " ", str(body["name"] or "")).strip()[:120] or None
    if "phone" in body:
        new["phone"] = re.sub(r"[^\d+ ]", "", str(body["phone"] or "")).strip()[:40] or None
    if "amount" in body:
        try:
            new["amount"] = round(float(str(body["amount"]).replace(",", "")), 2) if body["amount"] not in (None, "") else None
        except (TypeError, ValueError):
            raise ImportError_(400, "The amount must be a number.")
        if new["amount"] is not None and new["amount"] <= 0:
            raise ImportError_(400, "The amount must be more than zero.")
    if "entry_date" in body:
        try:
            new["entry_date"] = _dt.date.fromisoformat(str(body["entry_date"])[:10]) if body["entry_date"] else None
        except ValueError:
            raise ImportError_(400, "The date must look like 2026-11-09.")
    if "kind" in body:
        k = str(body["kind"] or "")
        allowed = ("opening",) if i["mode"] == "balances" else ("credit", "payment")
        if k not in allowed:
            raise ImportError_(400, "For this import a row is %s." % " or ".join(allowed))
        new["kind"] = k
    if "customer_id" in body:
        cid = body["customer_id"]
        if cid:
            L.balance(cur, cid)      # 404 when it is not this business's customer
        new["customer_id"] = cid or None
    changed = any(str(new[k] or "") != str(r[k] or "") for k in new)
    cur.execute("""UPDATE ledger_import_rows SET name = %s, phone = %s, amount = %s, entry_date = %s, kind = %s,
                          customer_id = %s, edited = edited OR %s, status = 'needs_check', decided_by = NULL, decided_at = NULL
                    WHERE id = %s""",
                (new["name"], new["phone"], new["amount"], new["entry_date"], new["kind"], new["customer_id"], changed, r["id"]))
    _refresh(cur, r["id"], i["mode"])
    return view(cur, iid)


def _row(cur, i, rid):
    if not UUID.match(str(rid)):
        raise ImportError_(404, "That row is not in this import.")
    cur.execute("SELECT * FROM ledger_import_rows WHERE id::text = %s AND import_id = %s", (str(rid), i["id"]))
    r = cur.fetchone()
    if not r:
        raise ImportError_(404, "That row is not in this import.")
    return r


def decide(cur, iid, rid, decision, actor):
    i = _imp(cur, iid)
    r = _row(cur, i, rid)
    if r["status"] == "imported":
        raise ImportError_(409, "That row is already in the ledger.")
    if decision == "approve":
        _refresh(cur, r["id"], i["mode"])
        r = _row(cur, i, rid)
        blocking = sorted(set(r["flags"]) & BLOCKING)
        if blocking:
            raise ImportError_(409, "Fix this row first: %s." % ", ".join(
                {"no_name": "whose line it is", "no_amount": "the amount",
                 "ambiguous_customer": "which customer (more than one has this name; add the phone or choose)"}[b]
                for b in blocking))
    elif decision != "reject":
        raise ImportError_(400, "Approve or reject.")
    cur.execute("UPDATE ledger_import_rows SET status = %s, decided_by = %s, decided_at = now(), import_error = NULL WHERE id = %s",
                ("approved" if decision == "approve" else "rejected", actor, r["id"]))
    return view(cur, iid)


def approve_ready(cur, iid, actor):
    """Every row with no flag at all. Flagged rows are always approved one by one."""
    i = _imp(cur, iid)
    cur.execute("SELECT id FROM ledger_import_rows WHERE import_id = %s AND status = 'ready'", (i["id"],))
    for r in cur.fetchall():
        _refresh(cur, r["id"], i["mode"])
    cur.execute("""UPDATE ledger_import_rows SET status = 'approved', decided_by = %s, decided_at = now()
                    WHERE import_id = %s AND status = 'ready'""", (actor, i["id"]))
    return view(cur, iid)


def commit(cur, iid, actor):
    """Write every approved row into the udhaar ledger, once. Rows that cannot be written stay approved with the reason."""
    i = _imp(cur, iid)
    if i["status"] != "open":
        raise ImportError_(409, "This import is closed.")
    cur.execute("""SELECT r.*, p.page_no FROM ledger_import_rows r JOIN ledger_import_pages p ON p.id = r.page_id
                    WHERE r.import_id = %s AND r.status = 'approved'
                    ORDER BY r.entry_date NULLS FIRST, (r.kind = 'payment'), p.page_no, r.row_no""", (i["id"],))
    rows = cur.fetchall()
    if not rows:
        raise ImportError_(400, "Approve at least one row first.")
    done = failed = 0
    for r in rows:
        cur.execute("SAVEPOINT reg_row")
        try:
            body = {"kind": r["kind"], "amount": float(r["amount"]), "occurred_on": str(r["entry_date"]) if r["entry_date"] else None,
                    "note": ("Register page %d: %s" % (r["page_no"], r["read_text"] or r["name"] or ""))[:300],
                    "client_ref": "imp" + str(r["id"]).replace("-", "")}
            if r["customer_id"]:
                body["customer_id"] = str(r["customer_id"])
            else:
                found = L.find(cur, r["name"], r["phone"])
                d = L._digits(r["phone"])
                exact = [c for c in found if len(d) >= 7 or c["name"].strip().lower() == (r["name"] or "").strip().lower()]
                if len(exact) > 1:
                    raise L.LedgerError(409, "More than one customer is called %s; choose which." % r["name"])
                if exact:
                    body["customer_id"] = exact[0]["customer_id"]
                else:
                    body["customer_name"], body["customer_phone"] = r["name"], r["phone"]
            out = L.add_entry(cur, body, actor=actor, source="import")
            cur.execute("""UPDATE ledger_import_rows SET status = 'imported', ledger_entry_id = %s, customer_id = %s,
                                  import_error = NULL WHERE id = %s""",
                        (out["entry"]["id"], out["customer"]["customer_id"], r["id"]))
            cur.execute("RELEASE SAVEPOINT reg_row")
            done += 1
        except L.LedgerError as e:
            cur.execute("ROLLBACK TO SAVEPOINT reg_row")
            cur.execute("UPDATE ledger_import_rows SET import_error = %s WHERE id = %s", (e.message[:300], r["id"]))
            failed += 1
    cur.execute("SELECT count(*) AS n FROM ledger_import_rows WHERE import_id = %s AND status IN ('needs_check','ready','approved')",
                (i["id"],))
    if not cur.fetchone()["n"]:
        cur.execute("UPDATE ledger_imports SET status = 'imported', committed_at = now(), committed_by = %s WHERE id = %s",
                    (actor, i["id"]))
    record(cur, action="register.import", resource="ledger_imports", resource_id=str(i["id"]), actor=actor,
           detail={"imported": done, "failed": failed})
    return dict(view(cur, iid), imported_now=done, failed_now=failed)


def discard(cur, iid, actor):
    i = _imp(cur, iid)
    cur.execute("SELECT count(*) AS n FROM ledger_import_rows WHERE import_id = %s AND status = 'imported'", (i["id"],))
    if cur.fetchone()["n"]:
        raise ImportError_(409, "Some rows are already in the ledger, so this import is kept. Reject the rest instead.")
    cur.execute("DELETE FROM ledger_imports WHERE id = %s", (i["id"],))
    record(cur, action="register.discard", resource="ledger_imports", resource_id=str(i["id"]), actor=actor, detail={})
    return {"ok": True}


def view(cur, iid):
    i = _imp(cur, iid)
    cur.execute("""SELECT id::text, page_no, mime, byte_size, status, error, model, read_at FROM ledger_import_pages
                    WHERE import_id = %s ORDER BY page_no""", (i["id"],))
    pages = [dict(p) for p in cur.fetchall()]
    cur.execute("""SELECT r.id::text, r.page_id::text, p.page_no, r.row_no, r.read_text, r.read_name, r.read_amount, r.read_date,
                          r.confidence, r.name, r.phone, r.amount, r.entry_date, r.kind, r.customer_id::text, r.flags, r.status,
                          r.edited, r.import_error, r.ledger_entry_id::text
                     FROM ledger_import_rows r JOIN ledger_import_pages p ON p.id = r.page_id
                    WHERE r.import_id = %s ORDER BY p.page_no, r.row_no""", (i["id"],))
    rows = []
    for r in cur.fetchall():
        r = dict(r)
        for k in ("read_amount", "amount", "confidence"):
            r[k] = float(r[k]) if r[k] is not None else None
        rows.append(r)
    count = {s: sum(1 for r in rows if r["status"] == s) for s in ("needs_check", "ready", "approved", "rejected", "imported")}
    kept = [r for r in rows if r["status"] in ("approved", "imported")]
    per = {}
    for r in kept:
        k = (r["name"] or "?").strip()
        per[k] = round(per.get(k, 0) + (-1 if r["kind"] == "payment" else 1) * (r["amount"] or 0), 2)
    acc = {"rows_read": len(rows), "kept": len(kept), "kept_as_read": sum(1 for r in kept if not r["edited"]),
           "rejected": count["rejected"],
           "accuracy": round(sum(1 for r in kept if not r["edited"]) / len(kept), 3) if kept else None}
    return {"import": {"id": str(i["id"]), "mode": i["mode"], "title": i["title"], "status": i["status"],
                       "created_at": i["created_at"], "committed_at": i["committed_at"]},
            "pages": pages, "rows": rows, "counts": count, "accuracy": acc,
            "totals_by_customer": [{"name": k, "total": v} for k, v in sorted(per.items(), key=lambda x: -x[1])][:200]}


def listing(cur):
    cur.execute("""SELECT i.id::text, i.mode, i.title, i.status, i.created_at,
                          (SELECT count(*) FROM ledger_import_pages p WHERE p.import_id = i.id) AS pages,
                          (SELECT count(*) FROM ledger_import_rows r WHERE r.import_id = i.id AND r.status = 'imported') AS imported
                     FROM ledger_imports i ORDER BY i.created_at DESC LIMIT 30""")
    return {"imports": [dict(r) for r in cur.fetchall()]}


# ------------------------------------------------------------------ routes
def make_register_router(identity):
    router = APIRouter(prefix="/ledger/imports", tags=["udhaar"])

    def who(h):
        tid, role, uid = identity(*h)
        if role not in L.MONEY_ROLES:
            raise HTTPException(403, "Bringing in the old register is for the owner.")
        return tid, role, str(uid or "")

    def run(fn, h):
        tid, role, uid = who(h)
        try:
            with tenant_session(tid, role) as cur:
                return fn(cur, uid)
        except (ImportError_, L.LedgerError) as e:
            raise HTTPException(e.status, e.message)

    def H(a, b, c, d):
        return (a, b, c, d)

    @router.get("")
    def imports_list(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        return run(lambda cur, uid: listing(cur), H(x_tenant_id, x_role, authorization, td_session))

    @router.post("")
    def imports_new(body: dict = Body(default={}), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Start bringing in the register: balances (what each customer owes now) or transactions."""
        return run(lambda cur, uid: create(cur, body, uid), H(x_tenant_id, x_role, authorization, td_session))

    @router.get("/{iid}")
    def imports_one(iid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        return run(lambda cur, uid: view(cur, iid), H(x_tenant_id, x_role, authorization, td_session))

    @router.post("/{iid}/pages")
    def imports_page(iid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Add one page: a JPEG or PNG photo, or a PDF, as a data URL. Kept private."""
        return run(lambda cur, uid: add_page(cur, iid, body, uid), H(x_tenant_id, x_role, authorization, td_session))

    @router.get("/{iid}/pages/{pid}/image")
    def imports_image(iid: str, pid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                      authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        mime, data = run(lambda cur, uid: page_bytes(cur, iid, pid), H(x_tenant_id, x_role, authorization, td_session))
        return Response(content=data, media_type=mime, headers={"Cache-Control": "private, no-store"})

    @router.post("/{iid}/pages/{pid}/read")
    def imports_read(iid: str, pid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Read one page with the AI into rows to check. Nothing reaches the ledger."""
        tid, role, uid = who(H(x_tenant_id, x_role, authorization, td_session))
        if not UUID.match(pid):
            raise HTTPException(404, "That page is not in this import.")
        try:
            return read(tid, role, iid, pid, uid)
        except ImportError_ as e:
            raise HTTPException(e.status, e.message)

    @router.patch("/{iid}/rows/{rid}")
    def imports_edit(iid: str, rid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                     x_role: str | None = Header(None), authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        return run(lambda cur, uid: edit_row(cur, iid, rid, body, uid), H(x_tenant_id, x_role, authorization, td_session))

    @router.post("/{iid}/rows/{rid}/decide")
    def imports_decide(iid: str, rid: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                       x_role: str | None = Header(None), authorization: str | None = Header(None),
                       td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        return run(lambda cur, uid: decide(cur, iid, rid, str(body.get("decision") or ""), uid),
                   H(x_tenant_id, x_role, authorization, td_session))

    @router.post("/{iid}/approve-ready")
    def imports_approve_ready(iid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                              authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        return run(lambda cur, uid: approve_ready(cur, iid, uid), H(x_tenant_id, x_role, authorization, td_session))

    @router.post("/{iid}/commit")
    def imports_commit(iid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                       authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Write the approved rows into the udhaar ledger."""
        return run(lambda cur, uid: commit(cur, iid, uid), H(x_tenant_id, x_role, authorization, td_session))

    @router.post("/{iid}/discard")
    def imports_discard(iid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                        authorization: str | None = Header(None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        return run(lambda cur, uid: discard(cur, iid, uid), H(x_tenant_id, x_role, authorization, td_session))

    return router
