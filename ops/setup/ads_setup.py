#!/usr/bin/env python3
"""Guided setup, run on the server:  python3 ops/setup/ads_setup.py

Turns on "Connect Google" and Google Ads results in every business's
dashboard. Asks for TechDeva's Google keys (typed hidden where secret), writes
them into the platform .env (a backup copy is kept first, mode 600), restarts
ONLY the api container, then shows what the dashboard now sees. Nothing is
printed back. Facebook & Instagram need no step here: they use the Meta app
that is already configured (META_APP_ID / META_APP_SECRET).

Where each value comes from (Google Cloud console, project "TechDeva"):
  GOOGLE_CLIENT_ID / GOOGLE_SECRET
      APIs & Services -> Credentials -> Create credentials -> OAuth client ID
      -> Web application; Authorised redirect URI:
         https://api.techdeva.net/oauth/google/callback
  GOOGLE_ADS_DEVELOPER_TOKEN
      ads.google.com with TechDeva's MANAGER account -> Tools -> API Center.
      Optional now: without it, clients can still connect Google; ad results
      appear once the token is approved.
  GOOGLE_ADS_LOGIN_CUSTOMER_ID
      TechDeva's manager account id (123-456-7890). Only needed when client
      ad accounts are reached through TechDeva's manager account.
"""
import getpass, os, shutil, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV = os.path.join(ROOT, ".env")


def ask(prompt, secret=False, required=True):
    v = (getpass.getpass(prompt + " (hidden): ") if secret else input(prompt + ": ")).strip().strip("\"'")
    if required and not v:
        sys.exit("%s was left empty; nothing changed." % prompt)
    if any(c in v for c in "\n\r'\" "):
        sys.exit("%s has a space, quote or line break in it; nothing changed." % prompt)
    return v


def main():
    if not os.path.isfile(ENV):
        sys.exit("platform .env not found at %s" % ENV)
    print(__doc__.split("Where each value")[0].strip() + "\n")
    cid = ask("Google OAuth client ID (ends in .apps.googleusercontent.com)")
    if not cid.endswith(".apps.googleusercontent.com"):
        sys.exit("That does not look like a Google OAuth client ID; nothing changed.")
    sec = ask("Google OAuth client secret", secret=True)
    dev = ask("Google Ads developer token (Enter to skip for now)", secret=True, required=False)
    mcc = "".join(c for c in ask("TechDeva manager account id, 123-456-7890 (Enter to skip)", required=False) if c.isdigit())
    if mcc and len(mcc) != 10:
        sys.exit("A Google Ads account id has 10 digits; nothing changed.")

    vals = {"GOOGLE_CLIENT_ID": cid, "GOOGLE_SECRET": sec}
    if dev:
        vals["GOOGLE_ADS_DEVELOPER_TOKEN"] = dev
    if mcc:
        vals["GOOGLE_ADS_LOGIN_CUSTOMER_ID"] = mcc
    backup = "%s.bak-%s" % (ENV, time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(ENV, backup)
    os.chmod(backup, 0o600)
    out, seen = [], set()
    for line in open(ENV).read().splitlines():
        k = line.split("=", 1)[0].strip()
        if k in vals:
            if k not in seen:
                out.append("%s=%s" % (k, vals[k]))
                seen.add(k)
            continue
        out.append(line)
    missing = [k for k in vals if k not in seen]
    if missing:
        out.append("# Google sign-in + Google Ads (ops/setup/ads_setup.py)")
        out += ["%s=%s" % (k, vals[k]) for k in missing]
    mode = os.stat(ENV).st_mode & 0o777
    with open(ENV, "w") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(ENV, mode)
    print("Wrote %s to .env (backup: %s)" % (", ".join(vals), backup))

    print("Restarting the api container only...")
    subprocess.run(["docker", "compose", "up", "-d", "--no-deps", "api"], cwd=ROOT, check=True)
    time.sleep(8)
    code = ("from app import oauth, ads\n"
            "print('google sign-in:', 'ready' if oauth.GOOGLE_CLIENT_ID and oauth.GOOGLE_SECRET else 'MISSING')\n"
            "print('google ads results:', 'ready' if ads.GADS_DEV_TOKEN else 'waiting for the developer token')\n"
            "print('facebook & instagram:', 'ready' if oauth.META_APP_ID and oauth.META_APP_SECRET else 'MISSING')\n"
            "print('redirect for Google:', oauth.OAUTH_REDIRECT_BASE + '/oauth/google/callback')\n")
    r = subprocess.run(["docker", "compose", "exec", "-T", "api", "python", "-c", code],
                       cwd=ROOT, capture_output=True, text=True)
    print((r.stdout or r.stderr).strip())
    print("\nIf the redirect above is not exactly the one in Google Cloud, sign-in fails with "
          "redirect_uri_mismatch.\nTo undo: cp %s %s && docker compose up -d --no-deps api" % (backup, ENV))


if __name__ == "__main__":
    main()
