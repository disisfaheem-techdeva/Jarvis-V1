# Jarvis Autopilot Gallery (24 Oct 2026)

A business uploads photos and videos. Jarvis then:

1. looks at each one;
2. writes the captions from the business's own config (`tenant_config`);
3. spreads the posts over the days and times the owner chose.

A person checks the posts and the owner approves them. The existing worker then publishes each post at its time to Facebook and Instagram. Google Business Profile has an interface and a stub only.

It lives under **Marketing › Autopilot Gallery**, a new tab at the end of Marketing. No other tab moves.

## How it fits the platform

It adds no new job system, no ORM and no second publishing path for the worker. Everything runs on the parts the platform already has:
- FastAPI;
- psycopg 3 with raw SQL;
- row-level security, through `tenant_session(tid, role)`;
- the Redis `events:queue` / `tasks:queue` driven by `app/worker.py`;
- the orchestrator rules;
- the agent contract.

```
upload ──> media_assets (storage: tenants/{tid}/marketing/yyyy/mm/{uuid}.ext)

POST /marketing/autopilot/generate
   └─ campaigns.create(): drip.build_schedule() → draft scheduled_posts (one per picture per platform)
   └─ event marketing.campaign_created
        rule 24 → content.analyze_media      (GalleryContentAgent: vision → one configured service)
        event marketing.media_analyzed
        rule 25 → marketing_apply(analysis) + content.caption_batch
        event marketing.captions_ready
        rule 26 → marketing_apply(captions)      → posts are drafts, campaign awaiting_approval

POST /marketing/campaigns/{id}/approve   (owner only; records approved_by/approved_at per post)

scheduler tick (worker, every 15 s) → publisher.tick()
   └─ marketing.posts_due {post_ids}  (approved, due, campaign active)
        rule 28 → publish_post per post   (GalleryPublishingAgent)
            publisher.publish_one(): claim FOR UPDATE SKIP LOCKED → meta_publish_client → record
            events marketing.post_published | ops.alert + marketing.reconnect_needed
        rule 29 (reconnect_needed) → notify owner;  rule 7 (ops.alert) → alert TechDeva
```

## Tables (db/78_autopilot_gallery.sql)

All three tables have:
- `tenant_id NOT NULL`;
- forced row-level security;
- the standard `tenant_isolation` policy.

The migration is additive and safe to run twice. It ends with a verify step.

### `media_assets`
- storage key (always tenant-prefixed) and public URL;
- type, size and dimensions;
- `sha256`, unique per business;
- Instagram `warnings`;
- `vision_summary` and `service_tag`.

### `marketing_campaigns`
- `status`: draft → awaiting_approval → active ⇄ paused → completed, or cancelled;
- `rule_config`, `timezone` and `page_id`;
- `paused_reason`: `meta_auth` when a lost login paused it;
- `captioning`: pending / running / done / failed;
- who approved it.

### `scheduled_posts`
- **One row per picture per platform.**
- Caption and hashtags, `caption_source` (ai / human_edited) and `caption_version`.
- `flags`, plus `flags_overridden` for when the owner approved anyway.
- `scheduled_at`, in UTC.
- `status`: draft / approved / publishing / published / failed / cancelled.
- `attempts`, `retry_after` and `last_error`.
- `external_post_id`: unique per business and platform.
- **A check constraint:** an approved, publishing or published post must have `approved_by` and `approved_at`.
- **A trigger:** the campaign and the picture must belong to the same business.

### Other changes in db/78
- **`tenant_config.marketing`:** per-business settings with a default:
  - `banned_phrases`;
  - `caption_language`;
  - upload limits.
- **A `risk_policies` row for `publish_post`.** It explains why the after-the-fact policy gate does not hold this task (see Decisions).

## Task types, events and rules

| Event | Rule | Action |
|---|---|---|
| `marketing.campaign_created` | 24 | content `content.analyze_media` |
| `marketing.media_analyzed` | 25 | `marketing_apply` (analysis), content `content.caption_batch` |
| `marketing.captions_ready` | 26 | `marketing_apply` (captions) |
| `marketing.recaption_requested` | 27 | content `content.recaption_one` |
| `marketing.caption_rewritten` | 27 | `marketing_apply` (recaption) |
| `marketing.posts_due` | 28 | publishing `publish_post`, one per post |
| `marketing.reconnect_needed` | 29 | notify owner |
| `marketing.post_published` | — | kept for Reporting / Growth; the payload carries `service_tag` |

`marketing_apply` is a new orchestrator action, applied by `Persistence.marketing_apply`. The agents only read. What they make travels in events, and the orchestrator writes it, always as drafts.

## API (`app/marketing/api.py`)

| Method and path | Who |
|---|---|
| `GET /marketing/media`, `POST /marketing/media` (multipart, files), `DELETE /marketing/media/{id}` | Marketing staff |
| `POST /marketing/autopilot/preview` (the live preview line), `POST /marketing/autopilot/generate` | Marketing staff |
| `GET /marketing/campaigns`, `GET /marketing/campaigns/{id}` | Marketing staff |
| `PATCH /marketing/posts/{id}` (caption, hashtags, scheduled_at, cancel), `POST /marketing/posts/{id}/recaption` | Marketing staff |
| `POST /marketing/campaigns/{id}/approve` (`override_post_ids`), `/pause`, `/resume`, `/cancel` | **Owner only**; the founder may not approve |
| `GET /media/g/{key}` | Public by link (Meta downloads from here) |

## External calls: stub, live, degraded (the `hermes_client` shape)

| Client | Stub (development, tests) | Live | Degraded |
|---|---|---|---|
| `app/marketing/vision_client.py` | `VISION_PROVIDER` unset: a summary from the file name | `VISION_PROVIDER=gemini` or `anthropic` | The post is flagged "vision_unavailable"; the campaign carries on |
| `app/marketing/captioner.py` | `CAPTION_MODE=stub`: a caption built from the config | The platform's AI chain (`app/ai.py`, metered) | An empty caption, flagged; the batch never crashes |
| `app/meta_publish_client.py` | `META_PUBLISH_MODE=stub`, or no `META_APP_ID`: the id starts with `stub:` and the screen says "test mode, not really posted" | The Graph API with the Page's token from `integrations` | Meta unreachable: a transient error, retried |

Defaults:
- **`CAPTION_MODE` is `auto`:** live when an AI key is configured, otherwise stub.
- **`META_PUBLISH_MODE`:** live when `META_APP_ID` is set, otherwise stub.

## Decisions (and why)

- **Paths are `/marketing/...`, not `/api/marketing/...`.** No route in this app has an `/api` prefix, and the role middleware already maps `/marketing` to the Marketing section.
- **The migration is a numbered file, `db/78`.** That is how every change since `schema.sql` ships.
- **The content tasks do not set `human_review`.** If they did, the orchestrator would park them and create a second approval card that approves nothing. The human gate is the campaign approval, recorded on each post, and enforced three times:
  - by a database constraint;
  - by the publishing claim, which requires `approved_by`;
  - by the approve endpoint, which is owner only.
- **Publishing is not gated by the policy table.** The orchestrator applies policy gates *after* an agent has run, so a "requires approval" policy on `publish_post` would act after the post was out. The approval is checked before the post is claimed instead. The `risk_policies` row says this.
- **Media addresses are public-by-link, not signed URLs.** The key holds a random uuid, the same rule `/media/<token>` already follows. Instagram downloads the file when it publishes, which can be weeks after upload, so a short-lived signed URL would expire first.
- **PNG and WEBP are stored as JPEG.** Instagram's API publishes only JPEG pictures.
- **The service tag is chosen in code,** from the business's own services, and only when the whole service name is seen. A shampoo bottle is not a "Hair Cut"; a test proves it.
- **Collisions are avoided across both systems.** The drip engine also avoids minutes already used by the Posts page's scheduled `social_posts`.
- **`meta_publish_client.py` copies the Graph calls instead of refactoring `social.py`.** This keeps the working Posts and Content Autopilot publishing untouched. Merging the two is a later, separate change.
- **Fix in shared code: `Persistence.has_open_task` compared `lead_id` only.** A second campaign's captions, or a second due post, looked like a duplicate and were dropped. It now also compares `post_id` and `campaign_id`; tasks without those are unaffected.

## Tests

| File | Covers | Count |
|---|---|---|
| `tests/test_gallery_drip.py` | Every case in the brief: 3+3+3+1, Tue/Thu/Sat over 5 weeks, mid-week start, today's time passed, collisions, several times a day, bad rules, Europe/London summer time both ways, determinism | 14 |
| `tests/test_gallery_captions.py` | The price guardrail, banned phrases, similarity, script, platform limits, picture → service, the reply shape with one corrective retry, empty flagged captions | 9 |
| `tests/test_gallery_flow.py` | The full flow below (real API, database, Redis, rules and worker) | 12 |

The flow tests cover:
- 15 uploads → 30 drafts at the right UTC times → approve → each post published exactly once at its time → campaign completed;
- unapproved posts are never published;
- two workers racing for one post publish it once;
- a 401 pauses the campaign, alerts TechDeva, notifies the owner, and leaves the rest of the posts untouched;
- transient errors are retried, then fail after 3 attempts;
- an edited approved caption needs approving again;
- flags block approval unless overridden;
- moving a post checks the future and other posts;
- isolation: through the API, through RLS, cross-tenant writes refused, admin sees all.

## Going live: checklist

1. **Database:** back up, then apply `db/78_autopilot_gallery.sql`. It prints "verify ok".
2. **Rebuild and restart api and worker.** This adds `python-multipart` to the image.
3. **Storage.** Either keep local disk, or move to a bucket:
   - **Local disk:** keep `MEDIA_DIR` on a volume that survives rebuilds. `OAUTH_REDIRECT_BASE` must be the public `https://` API address, because that is where Meta fetches the files from.
   - **Bucket (recommended at scale):** set `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_PUBLIC_BASE`, and optionally `S3_ENDPOINT` / `S3_REGION`, plus AWS keys. Allow public reads of `tenants/*`, and add `boto3` to the image.
4. **Vision:** set `VISION_PROVIDER=gemini`. `GEMINI_API_KEY` is already used for AI pictures.
5. **Meta App:**
   - permissions: `pages_manage_posts`, `pages_read_engagement`, `instagram_basic`, `instagram_content_publish`, `business_management`;
   - App Review approved for them (see `docs/META-APP-REVIEW.md`);
   - a real Page with a linked Instagram Business account for a first test;
   - `META_APP_ID` set makes publishing live.
6. **First real test:** one picture, one platform, a time 10 minutes ahead. Approve it, watch it appear on the Page, then delete it.
7. **Google Business Profile:** not live. `GBP_PUBLISH_ENABLED` only switches on the stub.

## For the techdeva-platform skill references

Paste into `references/architecture.md`:

> **Autopilot Gallery** (`app/marketing`, `db/78`). Tables `media_assets`, `marketing_campaigns`, `scheduled_posts` (RLS). Content agent task types `content.analyze_media`, `content.caption_batch`, `content.recaption_one`; publishing agent `publish_post`. Events `marketing.campaign_created`, `.media_analyzed`, `.captions_ready`, `.recaption_requested`, `.caption_rewritten`, `.posts_due` (scheduler), `.reconnect_needed`, `.post_published`. Rules 24–29. Orchestrator action `marketing_apply` (writes drafts only). Clients `vision_client`, `captioner`, `meta_publish_client` (stub / live / degraded).

Paste into `references/conventions.md`:

> Approval of anything public is recorded per row (`approved_by`, `approved_at`), enforced by a check constraint and checked again at claim time; the after-the-fact policy gate is not the human gate for publishing. A claim is `UPDATE … WHERE status='approved' … FOR UPDATE SKIP LOCKED`; an external id already set means never publish again. A stub external id starts with `stub:` and is labelled as test mode. Task payloads that name a post or a campaign carry `post_id` / `campaign_id`, which `has_open_task` uses to de-duplicate.
