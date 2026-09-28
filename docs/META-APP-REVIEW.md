# Meta App Review — step by step (for Faheem)

Only you can submit this: it is done in your Meta developer account, and Meta decides.
Everything the review checks on our side is now built. Expect 3–10 working days after
you submit.

## 1. Before you submit (5 minutes)

| Meta asks for | Put this | Status |
|---|---|---|
| Privacy Policy URL | `https://portal.techdeva.net/privacy` | built |
| Terms of Service URL | `https://portal.techdeva.net/terms` | built |
| User data deletion | choose **Data deletion instructions URL**: `https://portal.techdeva.net/data-deletion` | built (the form lands in founder console, Pipeline, as "delete my data") |
| App icon | 1024×1024 TechDeva logo | you upload |
| Category | Business and pages | you choose |
| Business verification | App Dashboard, then Verification | you do (needs business documents) |

If techdeva.net serves the same website, you can use `https://techdeva.net/...` instead.
Open each link in a private window first. They must load without a login.

## 2. Permissions to request

The platform uses these today (`app/oauth.py`, `META_SCOPES`):

| Permission | Why TechDeva needs it (paste into "How will your app use it") |
|---|---|
| `pages_show_list` | To show the business owner the Facebook Pages they manage so they can choose where their posts go. |
| `pages_manage_posts` | To publish to the business's own Page the posts the owner writes and approves in the TechDeva dashboard (now, or at a time they schedule). Nothing is posted without the owner pressing Post or Schedule. |
| `pages_read_engagement` | Required with pages_manage_posts to read the Page and the result of each post. |
| `instagram_basic` | To find the Instagram business account linked to the Page and show its username to the owner. |
| `instagram_content_publish` | To publish the owner-approved post (picture + caption) to the business's own Instagram account. |
| `business_management` | To list Pages and Instagram accounts that belong to the owner's Business Portfolio. |
| `ads_management` | See the recommendation below. |

**Recommendation (your decision):** the code only *reads* ad accounts (spend and leads for the
dashboard). It never creates or edits ads. `ads_read` covers that and is much easier to get
approved than `ads_management`. If you agree, change `ads_management` to `ads_read` in
`app/oauth.py` (one word), deploy, and reconnect Facebook once. If you plan to create ads from
TechDeva later, keep `ads_management` and show that feature in the video.

## 3. The screen recording (one video per permission group, or one video showing all)

Record on a laptop with a test business:

1. Open `https://api.techdeva.net/dashboard`, log in as the business owner.
2. **Integrations → Connect Facebook & Instagram.** Show the Facebook login and the permission screen, and pick the Page.
3. Back in the dashboard, the page shows **Connected: <Page name>**.
4. **Posts** (menu, AI / Automation). Write a caption, press **Add picture**, choose a JPEG, tick Facebook and Instagram, **Save as draft**.
5. Press **Post now** (press twice: it asks to confirm). Show the post appearing on the Facebook Page and the Instagram account.
6. Write a second post, pick a time a few minutes ahead, press **Schedule**. Show it under *Scheduled*, then show it published at that time.
7. Show **Cancel schedule** on another scheduled post (it returns to a draft: nothing is posted).
8. Open `https://portal.techdeva.net/privacy` and `/data-deletion`.

Give the reviewer a test login in the "Notes for reviewer" box: create a throwaway business
from the signup page, and give them that email and password, not a real client's.

## 4. After approval

* Switch the app from **Development** to **Live** (App Dashboard, top bar).
* Until it is Live, only people with a role on the app (you and testers) can connect Facebook.
  That is why Dr Irfan's connection works (you are an admin) and a new client's would not.
