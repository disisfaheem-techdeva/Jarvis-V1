#!/usr/bin/env python3
"""Guided setup, run on the server:  python3 ops/setup/email_setup.py

Turns on emailed "Forgot password?" links. Asks for the mail account, writes
SMTP_* / MAIL_FROM into the platform .env (a backup copy is kept first, mode
600), restarts ONLY the api container, then sends one test email to prove it.
The password is typed hidden and never printed.

Gmail: use an App Password (Google Account -> Security -> 2-Step Verification
-> App passwords), not your normal password. Host smtp.gmail.com, port 587.
"""
import getpass, os, shutil, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV = os.path.join(ROOT, ".env")
KEYS = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM")


def ask(prompt, default=""):
    v = input("%s%s: " % (prompt, " [%s]" % default if default else "")).strip()
    return v or default


def main():
    if not os.path.isfile(ENV):
        sys.exit("platform .env not found at %s" % ENV)
    print("Email for password-reset links. Gmail: smtp.gmail.com / 587 / your Gmail / an App Password.")
    host = ask("SMTP host", "smtp.gmail.com")
    port = ask("SMTP port", "587")
    user = ask("SMTP username (usually the email address)")
    pw = getpass.getpass("SMTP password / App Password (hidden): ").replace(" ", "") if "gmail" in host else getpass.getpass("SMTP password (hidden): ")
    sender = ask("Send as", "TechDeva <%s>" % user if user else "")
    test_to = ask("Send a test email to")
    if not (host and port.isdigit() and user and pw and sender and "@" in test_to):
        sys.exit("Something was left empty; nothing changed.")
    for v in (host, port, user, pw, sender):
        if "\n" in v or "\r" in v or "'" in v:
            sys.exit("Line breaks and ' are not allowed in these values; nothing changed.")

    backup = "%s.bak-%s" % (ENV, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(ENV, backup)
    os.chmod(backup, 0o600)
    vals = {"SMTP_HOST": host, "SMTP_PORT": port, "SMTP_USER": user, "SMTP_PASSWORD": pw, "MAIL_FROM": sender}
    quote = lambda v: "'" + v + "'" if any(c in v for c in " <>#\"$`\\") else v
    out, seen = [], set()
    for line in open(ENV).read().splitlines():
        k = line.split("=", 1)[0].strip()
        if k in vals:
            if k not in seen:
                out.append("%s=%s" % (k, quote(vals[k])))
                seen.add(k)
            continue
        out.append(line)
    missing = [k for k in KEYS if k not in seen]
    if missing:
        out.append("# email for Forgot password links (ops/setup/email_setup.py)")
        out += ["%s=%s" % (k, quote(vals[k])) for k in missing]
    mode = os.stat(ENV).st_mode & 0o777
    with open(ENV, "w") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(ENV, mode)
    print("Wrote %s to .env (backup: %s)" % (", ".join(KEYS), backup))

    print("Restarting the api container only...")
    subprocess.run(["docker", "compose", "up", "-d", "--no-deps", "api"], cwd=ROOT, check=True)
    time.sleep(6)
    code = ("import sys\nfrom app.account import mail_configured, send_mail\n"
            "assert mail_configured(), 'SMTP settings did not reach the api container'\n"
            "send_mail(sys.argv[1], 'TechDeva test email', 'Password-reset emails are working.')\nprint('sent')")
    r = subprocess.run(["docker", "compose", "exec", "-T", "api", "python", "-c", code, test_to],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode == 0 and "sent" in r.stdout:
        print("PASS: test email sent to %s. Check the inbox (and spam)." % test_to)
    else:
        err = (r.stderr or r.stdout).strip().splitlines()
        print("FAIL: the mail server refused: %s" % (err[-1] if err else "unknown error"))
        print("Nothing else changed. Fix the details and run this again; to undo: cp %s %s && docker compose up -d --no-deps api" % (backup, ENV))


if __name__ == "__main__":
    main()
