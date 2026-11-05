"""POS settings and manager approval (3 Nov 2026).

Settings are the business's own (receipt text, tax, whether the till needs an
open shift). The hardware a till uses (printer, drawer, scanner, paper width
on THAT machine) is the device's, kept in the browser, because two counters
of one shop have different printers.

Manager approval: a cashier's till asks for the owner's (or a Manager's) PIN
for the money decisions the cashier cannot make alone: a discount, a refund,
cancelling a bill, removing a line the kitchen already has, an udhaar sale.
A right PIN gives a signed approval that is good for ONE kind of action for
five minutes; the action records who approved it. Wrong PINs count against
the same lockout as the PIN login (pins.py: 5 per screen, 30 per business, 15
minutes), so the approval box is not a way round it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

from fastapi import HTTPException

from .audit import record
from .auth import _b64u, _secret, _unb64u, verify_password
from .db import tenant_session

TAX_MODES = ("off", "exclusive", "inclusive")
APPROVAL_ACTIONS = ("discount", "refund", "cancel", "void", "credit", "price")
APPROVAL_TTL = 300
IP_FAILS, TENANT_FAILS, WINDOW_MIN = 5, 30, 15

DEFAULTS = {
    "receipt_header": "",        # shown under the business name: address, phone
    "receipt_footer": "Thank you!",
    "ntn": "",                   # tax number, printed when set
    "tax_mode": "off",           # never on unless the owner turns it on
    "tax_rate": 0.0,
    "tax_label": "GST",
    "require_shift": True,       # no sale without an open drawer
    "allow_staff_credit": False, # udhaar by a cashier without the owner's PIN
    "kitchen_tickets": False,    # print a KOT when a bill is kept open / added to
    "registers": ["Till 1"],
}


class PosSettingsError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _text(v, n):
    return " ".join(str(v or "").split())[:n] if n < 100 else str(v or "").strip()[:n]


def clean(body, current=None):
    """Only known keys, each checked. Unknown keys are dropped, not stored."""
    s = dict(DEFAULTS)
    s.update(current or {})
    b = body or {}
    if "receipt_header" in b:
        s["receipt_header"] = _text(b["receipt_header"], 300)
    if "receipt_footer" in b:
        s["receipt_footer"] = _text(b["receipt_footer"], 200)
    if "ntn" in b:
        s["ntn"] = _text(b["ntn"], 40)
    if "tax_label" in b:
        s["tax_label"] = _text(b["tax_label"], 20) or "Tax"
    if "tax_mode" in b:
        if b["tax_mode"] not in TAX_MODES:
            raise PosSettingsError(400, "Tax must be off, added to prices, or included in prices.")
        s["tax_mode"] = b["tax_mode"]
    if "tax_rate" in b:
        try:
            r = round(float(b["tax_rate"]), 3)
        except (TypeError, ValueError):
            raise PosSettingsError(400, "The tax rate must be a number.")
        if not 0 <= r <= 100:
            raise PosSettingsError(400, "The tax rate must be between 0 and 100.")
        s["tax_rate"] = r
    for k in ("require_shift", "allow_staff_credit", "kitchen_tickets"):
        if k in b:
            s[k] = bool(b[k])
    if "registers" in b:
        regs = [_text(x, 40) for x in (b["registers"] or []) if _text(x, 40)]
        if not regs or len(regs) > 20:
            raise PosSettingsError(400, "Name between 1 and 20 tills.")
        s["registers"] = list(dict.fromkeys(regs))
    if s["tax_mode"] != "off" and s["tax_rate"] <= 0:
        raise PosSettingsError(400, "Set the tax rate, or turn tax off.")
    return s


def get(cur):
    cur.execute("SELECT settings FROM pos_settings WHERE tenant_id = current_tenant()")
    r = cur.fetchone()
    return clean({}, r["settings"] if r else None)


def save(cur, body, *, actor):
    s = clean(body, get(cur))
    cur.execute("""INSERT INTO pos_settings (tenant_id, settings, updated_by) VALUES (current_tenant(), %s::jsonb, %s)
                   ON CONFLICT (tenant_id) DO UPDATE SET settings = EXCLUDED.settings, updated_by = EXCLUDED.updated_by,
                                                         updated_at = now()""", (json.dumps(s), str(actor or "") or None))
    record(cur, action="pos.settings", resource="pos_settings", actor=str(actor or ""), detail=s)
    return s


# ------------------------------------------------------------------ approvals
def _sign(payload):
    raw = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    mac = hmac.new(_secret(), ("pos-approval." + raw).encode(), hashlib.sha256).digest()
    return raw + "." + _b64u(mac)


def verify_approval(token, *, tenant_id, action):
    """The approver's user id when the token is good for this business and
    action, else None. Never raises: a bad token simply approves nothing."""
    try:
        raw, mac = str(token or "").split(".", 1)
        good = hmac.new(_secret(), ("pos-approval." + raw).encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(good, _unb64u(mac)):
            return None
        p = json.loads(_unb64u(raw))
    except Exception:
        return None
    if p.get("t") != str(tenant_id) or p.get("a") != action or int(p.get("exp", 0)) < time.time():
        return None
    return p.get("u")


def approve(tenant_id, pin, action, *, ip, asked_by):
    """Check a manager PIN; return a five-minute approval for one action."""
    if action not in APPROVAL_ACTIONS:
        raise PosSettingsError(400, "Unknown approval.")
    pin = str(pin or "").strip()
    if not pin.isdigit() or not 4 <= len(pin) <= 6:
        raise PosSettingsError(400, "Enter a 4 to 6 digit PIN.")
    from .roles import permissions_for
    ip = "pos:" + str(ip or "?")[:60]
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT count(*) FILTER (WHERE ip = %s) AS by_ip, count(*) AS all_
                         FROM pin_attempts WHERE tenant_id = %s AND NOT ok
                          AND at > now() - make_interval(mins => %s)""", (ip, tenant_id, WINDOW_MIN))
        f = cur.fetchone()
        if f["by_ip"] >= IP_FAILS or f["all_"] >= TENANT_FAILS:
            raise PosSettingsError(429, "Too many wrong PINs. Wait 15 minutes.")
        cur.execute("SELECT id, name, pin_hash FROM users WHERE tenant_id = %s AND pin_hash IS NOT NULL "
                    "AND role::text = 'owner'", (tenant_id,))
        who = next((u for u in cur.fetchall() if verify_password(pin * 2, u["pin_hash"])), None)
        if who:
            # a restricted owner-base role (Marketing, HR...) is not a manager:
            # only the real owner (no role) or a role that may approve money
            perms = permissions_for(str(who["id"]))
            if perms is not None and "approve" not in (perms["permissions"].get("finance") or []):
                who = None
        cur.execute("INSERT INTO pin_attempts (tenant_id, user_id, ip, ok) VALUES (%s, %s, %s, %s)",
                    (tenant_id, who["id"] if who else None, ip, bool(who)))
        record(cur, action="pos.approval", resource="users", resource_id=str(who["id"]) if who else None,
               actor=str(asked_by or ""), status="ok" if who else "refused",
               detail={"action": action}, tenant_id=tenant_id)
    # raised after the block commits, so the failed try is counted. 403, not
    # 401: the cashier's own session is fine, and a till treats 401 as signed out
    if not who:
        raise PosSettingsError(403,"That PIN cannot approve this. Ask the owner or a manager.")
    exp = int(time.time()) + APPROVAL_TTL
    return {"approval": _sign({"t": str(tenant_id), "u": str(who["id"]), "a": action, "exp": exp}),
            "approved_by": who["name"], "expires_in": APPROVAL_TTL}


def authority(role, tenant_id, approval, action):
    """(allowed, approver user id or None). The owner needs no approval."""
    from .orders import MONEY_ROLES
    if role in MONEY_ROLES:
        return True, None
    # one request may carry several approvals (a discount AND udhaar): {action: token}
    if isinstance(approval, dict):
        approval = approval.get(action)
    uid = verify_approval(approval, tenant_id=tenant_id, action=action)
    return (uid is not None), uid


def need(role, tenant_id, approval, action, message):
    ok, by = authority(role, tenant_id, approval, action)
    if not ok:
        raise PosSettingsError(403, message)
    return by


def as_http(e):
    raise HTTPException(e.status, e.message)
