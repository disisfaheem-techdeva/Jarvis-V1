# Connecting Facebook, Instagram and Google Ads (Part 18)

What every business gets on its dashboard, once TechDeva's side below is done:

- **Integrations → Ads & social accounts**
  - **Connect Facebook & Instagram**: one login gives page posting, the Instagram account linked to each page, and the ad accounts.
  - **Connect Google**: a Google Ads account and the Business Profile.
  - For each: the pages or Instagram accounts found, **Choose ad account**, **Reconnect**, **Disconnect**.
- **Marketing → Ad results**
  - Live spend, times shown, clicks, leads, WhatsApp chats and cost per result.
  - Per campaign, for the last 7, 30 or 90 days, from both platforms.
  - Refreshed every 15 minutes.
  - Read-only: nothing on the dashboard creates, changes or pauses an ad.
- **Who sees it:** only the owner connects accounts or sees ad spend. Staff see neither.
- **Where logins are kept:** encrypted, one business per row. The dashboard never shows a login token.

## TechDeva's side (once, for all clients)

### Facebook & Instagram (Meta app, already configured)
1. **App Review.** The Meta app needs these permissions approved: `ads_management` (or `ads_read`), `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`, `instagram_basic`, `instagram_content_publish` and `business_management`. See `docs/META-APP-REVIEW.md`.
2. **Before approval,** only people added to the app as testers or admins can connect.
3. **Each client's Instagram** must be a Business or Creator account linked to their Facebook page. The dashboard tells them when it isn't.

### Google (new)
1. **Create the sign-in client.** In the Google Cloud console, project "TechDeva":
   - **APIs & Services → Library:** enable the **Google Ads API**, and optionally the **My Business Business Information API**.
   - **OAuth consent screen:** External, app name TechDeva. Add your privacy and terms pages.
   - **Credentials → Create credentials → OAuth client ID → Web application.** Authorised redirect URI: `https://api.techdeva.net/oauth/google/callback`.
2. **Get a developer token.** At ads.google.com, sign in with TechDeva's **manager (MCC)** account and go to **Tools → API Center**. Apply for the token and ask for **Basic access**; Google usually takes a few days. Clients can connect before it's approved; results appear once it is.
3. **Enter the keys on the server:**
   ```
   cd /opt/techdeva-platform/techdeva-platform
   python3 ops/setup/ads_setup.py
   ```
   The script asks for the keys (secrets are typed hidden), backs up `.env`, restarts only the api and shows what is ready.
4. **Google verification.** The Google Ads permission is a "sensitive scope". Until Google verifies the app, only test users added on the consent screen can connect (up to 100), and they see an "unverified app" warning. Submit the app for verification when ready.

## Check it on the server
```
python3 ops/verify/live_check.py phase13
```
