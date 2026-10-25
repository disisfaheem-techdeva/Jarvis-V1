"""Email verification by 6-digit code (Faheem, 25 Oct 2026).

Runs against a SCRATCH database only (db/79 applied). Every account it makes is a
throwaway tenant tagged with TAG, deleted at the end. No real email is sent:
account.send_mail is replaced in every test that could reach it.
"""
import os
import uuid

import pytest

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from starlette.testclient import TestClient  # noqa: E402

from app import account, email_codes  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402

TAG = "emailcode-test-" + uuid.uuid4().hex[:8]
PW = "Correct-Horse-Battery-9"


@pytest.fixture(autouse=True)
def _clean_limits():
    email_codes._hits.clear()
    yield


@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("DELETE FROM tenants WHERE name LIKE %s", (TAG + "%",))
        cur.execute("DELETE FROM users WHERE email LIKE %s", (TAG + "%",))
        cur.execute("DELETE FROM signups WHERE business_name LIKE %s", (TAG + "%",))


@pytest.fixture
def mail(monkeypatch):
    """Capture mail instead of sending. mail.fail = True makes the SMTP call raise."""
    class Box(list):
        fail = False

        def code(self):
            return self[-1]["text"].split("code is:")[1].split()[0]

    box = Box()

    def send(to, subject, text):
        if box.fail:
            raise ConnectionError("SMTP server disconnected")
        box.append({"to": to, "subject": subject, "text": text})

    monkeypatch.setattr(account, "send_mail", send)
    monkeypatch.setattr(account, "mail_configured", lambda: True)
    return box


def _user(verified=False, email=None):
    email = email or "%s-%s@example.org" % (TAG, uuid.uuid4().hex[:6])
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (name, industry, status) VALUES (%s, 'test', 'active') RETURNING id",
                    (TAG + " biz",))
        tid = cur.fetchone()["id"]
        cur.execute("""INSERT INTO users (tenant_id, name, email, role, password_hash, email_verified_at)
                       VALUES (%s, 'Owner', %s, 'owner', %s, CASE WHEN %s THEN now() END)
                       RETURNING id, tenant_id, name, email""",
                    (tid, email, hash_password(PW), verified))
        return dict(cur.fetchone())


def _age_codes(user_id, seconds):
    """Pretend the user's codes were made `seconds` ago (resend gap / expiry)."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""UPDATE email_codes SET created_at = created_at - make_interval(secs => %s),
                                              expires_at = expires_at - make_interval(secs => %s)
                        WHERE user_id = %s""", (seconds, seconds, str(user_id)))


def _issue(user_id):
    with tenant_session(None, "techdeva_admin") as cur:
        return email_codes.issue(cur, user_id)


def _check(user_id, code):
    with tenant_session(None, "techdeva_admin") as cur:
        return email_codes.check(cur, user_id, code)


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


# ------------------------------------------------------------------ the code itself
def test_code_is_six_digits_and_only_its_hmac_is_stored():
    u = _user()
    code = _issue(u["id"])
    assert len(code) == 6 and code.isdigit()
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT code_hash FROM email_codes WHERE user_id = %s", (str(u["id"]),))
        stored = cur.fetchone()["code_hash"]
    assert code not in stored
    assert stored == email_codes.hash_code(u["id"], code)
    assert stored != email_codes.hash_code(uuid.uuid4(), code)      # bound to the user


def test_right_code_works_once():
    u = _user()
    code = _issue(u["id"])
    assert _check(u["id"], code) is True
    assert _check(u["id"], code) is False


def test_expired_code_is_refused():
    u = _user()
    code = _issue(u["id"])
    _age_codes(u["id"], 11 * 60)
    assert _check(u["id"], code) is False


def test_five_wrong_tries_kill_the_code():
    u = _user()
    code = _issue(u["id"])
    wrong = "%06d" % ((int(code) + 1) % 10 ** 6)
    for _ in range(5):
        assert _check(u["id"], wrong) is False
    assert _check(u["id"], code) is False


def test_new_code_invalidates_the_old_one():
    u = _user()
    old = _issue(u["id"])
    _age_codes(u["id"], 61)
    new = _issue(u["id"])
    assert _check(u["id"], old) is False or old == new
    assert _check(u["id"], new) is True


def test_resend_limits_one_a_minute_five_an_hour():
    u = _user()
    _issue(u["id"])
    with pytest.raises(email_codes.TooSoon):
        _issue(u["id"])
    for _ in range(4):
        _age_codes(u["id"], 61)
        _issue(u["id"])
    _age_codes(u["id"], 61)
    with pytest.raises(email_codes.TooSoon):           # sixth within the hour
        _issue(u["id"])


# ------------------------------------------------------------------ mail
def test_send_code_says_so_when_mail_is_not_configured(monkeypatch):
    sent = []
    monkeypatch.setattr(account, "mail_configured", lambda: False)
    monkeypatch.setattr(account, "send_mail", lambda *a: sent.append(a))
    out = email_codes.send_code(_user())
    assert out["sent"] is False and out["reason"] == "no_mail" and not sent


def test_send_code_never_claims_a_failed_send(mail):
    mail.fail = True
    out = email_codes.send_code(_user())
    assert out == {"sent": False, "reason": "failed", "wait": 0}


def test_send_code_emails_the_code(mail):
    u = _user()
    out = email_codes.send_code(u)
    assert out["sent"] is True
    assert mail[-1]["to"] == u["email"]
    assert "10 minutes" in mail[-1]["text"] and "wasn't you" in mail[-1]["text"]
    assert _check(u["id"], mail.code()) is True


# ------------------------------------------------------------------ the API
def test_login_blocked_until_verified_then_allowed(client, mail):
    u = _user(verified=False)
    r = client.post("/auth/login", json={"email": u["email"], "password": PW})
    assert r.status_code == 403
    assert r.json()["detail"] == "email not verified" and r.json()["verification_required"] is True
    assert "set-cookie" not in r.headers and "token" not in r.json()
    r = client.post("/auth/verify-email", json={"email": u["email"], "code": mail.code()})
    assert r.status_code == 200 and r.json()["verified"] is True
    r = client.post("/auth/login", json={"email": u["email"], "password": PW})
    assert r.status_code == 200 and r.json()["token"]


def test_wrong_password_still_401_not_403(client, mail):
    u = _user(verified=False)
    r = client.post("/auth/login", json={"email": u["email"], "password": "wrong-password-123"})
    assert r.status_code == 401 and not mail


def test_existing_verified_user_logs_in_as_before(client, mail):
    u = _user(verified=True)
    r = client.post("/auth/login", json={"email": u["email"], "password": PW})
    assert r.status_code == 200 and r.json()["token"] and not mail


def test_backfill_left_nobody_unverified_from_before_79():
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT count(*) AS n FROM users u
                        WHERE u.email_verified_at IS NULL AND u.email NOT LIKE %s
                          AND u.created_at < (SELECT min(verified_at) FROM email_verification_backfill)""",
                    (TAG + "%",))
        assert cur.fetchone()["n"] == 0


def test_pin_accounts_are_exempt():
    assert email_codes.exempt("pin+abc@pin.invalid")
    assert not email_codes.exempt("owner@example.org")


def test_verify_answers_the_same_for_unknown_email_and_wrong_code(client, mail):
    u = _user()
    email_codes.send_code(u)
    a = client.post("/auth/verify-email", json={"email": "nobody-%s@example.org" % TAG, "code": "123456"})
    wrong = "%06d" % ((int(mail.code()) + 1) % 10 ** 6)
    b = client.post("/auth/verify-email", json={"email": u["email"], "code": wrong})
    assert a.status_code == b.status_code == 400
    assert a.json() == b.json() == {"detail": "invalid or expired code"}


def test_wrong_guesses_through_the_api_count(client, mail):
    u = _user()
    email_codes.send_code(u)
    wrong = "%06d" % ((int(mail.code()) + 1) % 10 ** 6)
    for _ in range(5):
        client.post("/auth/verify-email", json={"email": u["email"], "code": wrong})
    r = client.post("/auth/verify-email", json={"email": u["email"], "code": mail.code()})
    assert r.status_code == 400


def test_resend_is_generic_and_rate_limited(client, mail):
    u = _user()
    a = client.post("/auth/resend-code", json={"email": u["email"]})
    b = client.post("/auth/resend-code", json={"email": "nobody-%s@example.org" % TAG})
    assert a.status_code == b.status_code == 200 and a.json() == b.json()
    assert len(mail) == 1
    client.post("/auth/resend-code", json={"email": u["email"]})     # within 60 s: no second email
    assert len(mail) == 1


def test_resend_skips_verified_accounts(client, mail):
    u = _user(verified=True)
    assert client.post("/auth/resend-code", json={"email": u["email"]}).status_code == 200
    assert not mail


def _signup(client, email):
    return client.post("/signup", json={"business_name": TAG + " signup", "contact_email": email,
                                        "contact_name": "Owner", "password": PW},
                       headers={"x-forwarded-for": "10.9.%d.%d" % tuple(os.urandom(2))})


def test_signup_sends_a_code_and_requires_verification(client, mail):
    email = "%s-su-%s@example.org" % (TAG, uuid.uuid4().hex[:6])
    r = _signup(client, email)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["verification_required"] is True and d["email_sent"] is True
    assert mail[-1]["to"] == email
    assert client.post("/auth/login", json={"email": email, "password": PW}).status_code == 403
    assert client.post("/auth/verify-email", json={"email": email, "code": mail.code()}).status_code == 200
    assert client.post("/auth/login", json={"email": email, "password": PW}).status_code == 200


def test_signup_still_succeeds_when_sending_fails(client, mail):
    mail.fail = True
    email = "%s-sf-%s@example.org" % (TAG, uuid.uuid4().hex[:6])
    r = _signup(client, email)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["email_sent"] is False and d["verification_required"] is True and d["email_message"]
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT 1 FROM users WHERE email = %s", (email,))
        assert cur.fetchone()                                        # the account exists
    mail.fail = False
    _age_codes(_find(email), 61)
    client.post("/auth/resend-code", json={"email": email})
    assert client.post("/auth/verify-email", json={"email": email, "code": mail.code()}).status_code == 200


def _find(email):
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT id FROM users WHERE email = %s", (email,))
        return cur.fetchone()["id"]


def test_set_password_from_a_link_marks_the_email_verified(client):
    from app.auth import issue_setup_token
    u = _user(verified=False)
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (str(u["id"]),))
        ph = cur.fetchone()["password_hash"]
    r = client.post("/auth/set-password", json={"token": issue_setup_token(u["email"], ph), "password": "A-new-long-pass-77"})
    assert r.status_code == 200, r.text
    assert email_codes.is_verified(u["id"])
