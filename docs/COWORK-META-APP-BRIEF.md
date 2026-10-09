# Brief for Cowork: fix "App not active" on Facebook connect

**Owner:** Faheem (TechDeva). **Date:** 2 October 2026.
**Where:** developers.facebook.com (Faheem's Meta developer account, logged in on his computer).
**Symptom:** a client presses **Connect Facebook & Instagram** in the TechDeva dashboard and Facebook shows **"App not active"**.

That message comes from Meta, not from TechDeva. It means Meta is not letting this app log people in:
the app is still in **Development** mode (only people with a role on the app can use it), it is
unpublished, or Meta has restricted it. Your job is to find which one and fix what Faheem is allowed to fix.

## Rules
- Work only in developers.facebook.com and business.facebook.com. Do not touch the server, the clinic CRM, WhatsApp or Hermes.
- **Never reset or change the App Secret** and never paste it anywhere. The platform uses it; changing it breaks every client's connection.
- **Never delete the app** or create a new one. The App ID is already set on the server.
- Do not submit App Review without showing Faheem first.
- Take a screenshot of each screen you check and give them to Faheem at the end.

## 1. Find the right app
- [ ] Open https://developers.facebook.com/apps and open the TechDeva app (the one used for "Connect Facebook").
- [ ] Note the **App ID** (top of the dashboard) and tell Faheem. He can confirm it matches the server by opening
      `https://api.techdeva.net/oauth/meta/diagnose` while logged in to the founder console (it shows `app_id`, never the secret).

## 2. Check why it is not active
- [ ] Top bar: is **App Mode** "Development" or "Live"? Screenshot.
- [ ] **Alerts / notifications** (bell icon, and App Dashboard → "Required actions"): any restriction, policy violation,
      "app disabled", or "data use checkup" due? Screenshot every alert.
- [ ] **App settings → Basic:** is anything marked missing in red? Screenshot.

## 3. Fill in what Live mode needs (App settings → Basic)
| Field | Value |
|---|---|
| App Domains | `techdeva.net` |
| Privacy Policy URL | `https://portal.techdeva.net/privacy` |
| Terms of Service URL | `https://portal.techdeva.net/terms` |
| User data deletion | **Data deletion instructions URL**: `https://portal.techdeva.net/data-deletion` |
| App icon | TechDeva logo, 1024×1024 |
| Category | Business and pages |

Open the three links in a private window first; they must load without logging in. Save changes.

## 4. Facebook Login settings
- [ ] Products → **Facebook Login for Business** (or Facebook Login) → **Settings**.
- [ ] **Valid OAuth Redirect URIs** must contain exactly: `https://api.techdeva.net/oauth/meta/callback`
- [ ] "Client OAuth login" and "Web OAuth login": **On**. "Enforce HTTPS": **On**. Save.

## 5. Data use checkup and verification
- [ ] If App Dashboard asks for **Data Use Checkup** or an **annual certification**, complete it (it is a short form, answer truthfully: the app publishes the business's own posts and reads its ad results).
- [ ] **Business verification** (App settings → Basic → Verification, or business.facebook.com → Security Centre): note whether it is Verified, Pending or Not started. If not started, stop and tell Faheem: it needs his business documents.

## 6. Switch to Live
- [ ] Top bar: switch **App Mode** to **Live**. If Meta refuses, screenshot the reason it gives.
- Note: in Live mode, clients can log in, but Meta only gives the posting permissions
  (`pages_manage_posts`, `instagram_content_publish`, `ads_management` / `ads_read`, `business_management`)
  to people with a role on the app **until App Review approves them**. The App Review steps are in
  `docs/META-APP-REVIEW.md`. Do not submit; prepare and show Faheem.

## 7. Until App Review is approved: let a client connect as a tester
For a client who must connect now:
- [ ] App roles → **Roles** → **Add people** → role **Tester** → their Facebook name or profile.
- [ ] The client must accept the invitation at https://developers.facebook.com/requests (logged in as themselves).
- [ ] Then they press **Connect Facebook & Instagram** again in their dashboard.

## 8. Report back to Faheem
- App mode now (Development / Live), and the reason if it could not be switched.
- Every alert or restriction found, with screenshots.
- Business verification status.
- Which fields you filled in, and that the redirect URI matches.
- Any client added as tester.
