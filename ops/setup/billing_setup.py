#!/usr/bin/env python3
"""Guided setup, run on the server:  python3 ops/setup/billing_setup.py

  1. How clients pay you   -> the text every client sees on Plan & Billing
  2. Each client's monthly fee

It asks; you type. Nothing is guessed: Enter keeps what is already there.
Talks to the live API with a short-lived founder token minted inside the api
container (no password typed or printed). Standard library only.
"""
import json, os, subprocess, sys, urllib.error, urllib.request

API = os.environ.get("API", "https://api.techdeva.net")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCAL_PY = os.environ.get("TD_LOCAL_PY")


def token():
    code = ("from app.auth import issue_token;"
            "print(issue_token(tenant_id=None,user_id='billing-setup',role='techdeva_admin',ttl=1800))")
    base = [LOCAL_PY] if LOCAL_PY else ["docker", "compose", "exec", "-T", "api", "python"]
    return subprocess.run(base + ["-c", code], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


TOK = None


def call(method, path, body=None):
    req = urllib.request.Request(API + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + TOK})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        sys.exit("API refused %s %s: HTTP %s %s" % (method, path, e.code, e.read().decode()[:200]))


def ask(prompt):
    try:
        return input(prompt)
    except EOFError:
        return ""


def main():
    global TOK
    TOK = token()

    # ---- 1. payment instructions
    cur = call("GET", "/techdeva/payment-claims").get("payment_instructions") or ""
    print("\n=== 1. How clients pay you (shown on every client's Plan & Billing page)")
    print("Now:\n  " + (cur.replace("\n", "\n  ") if cur else "(nothing yet)"))
    print("Type the new text, line by line, e.g.\n"
          "  <your bank>, account title, account number, IBAN\n"
          "  JazzCash / Easypaisa number (if you take them)\n"
          "  After paying, press 'I have paid' and add the transaction ID.\n"
          "Finish with an empty line. Just press Enter now to keep it as it is.")
    lines = []
    while True:
        line = ask("> " if not lines else "  ")
        if not line.strip():
            break
        lines.append(line.rstrip())
    if lines:
        text = "\n".join(lines)
        call("PUT", "/techdeva/settings/payment_instructions", {"value": text})
        back = call("GET", "/techdeva/payment-claims").get("payment_instructions")
        print("Saved and read back: %s" % ("OK" if back == text else "MISMATCH, check the founder console"))
    else:
        print("Kept.")

    # ---- 2. monthly fees
    print("\n=== 2. Monthly fee per client (PKR). Enter keeps the current fee; 0 means no fee yet.")
    clients = call("GET", "/techdeva/book").get("clients") or []
    changed = 0
    for c in clients:
        now = float(c.get("monthly_fee") or 0)
        raw = ask("%-40s now PKR %s  new: " % ((c.get("name") or "Unnamed")[:40], format(round(now), ","))).strip()
        if not raw:
            continue
        try:
            fee = float(raw.replace(",", ""))
            if fee < 0:
                raise ValueError
        except ValueError:
            print("  not a number, skipped")
            continue
        # send every field back as it is: this endpoint replaces the whole row
        body = {k: c.get(k) for k in ("setup_fee", "performance_pct", "started_on", "next_invoice_on", "contact", "notes")}
        body["monthly_fee"] = fee
        call("PUT", "/techdeva/clients/%s/account" % c["id"], body)
        changed += 1
        print("  saved: PKR %s a month" % format(round(fee), ","))
    print("\n%d fee(s) changed. Each client now sees it on Plan & Billing." % changed)


if __name__ == "__main__":
    main()
