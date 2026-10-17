# Launching instantgrill.shop

About an hour, in this order. Nothing here needs code changes.

## 1. Supabase (database)

1. Create a project at supabase.com (region: closest to Pakistan, e.g. Mumbai or Singapore).
2. SQL editor > paste `db/migrations/001_init.sql` > Run. It creates the tables, the customers view,
   and turns on row-level security so Supabase's public API keys can read nothing.
3. Project Settings > Database > Connection string > URI (Transaction pooler). That is `DATABASE_URL`.

## 2. Vercel (hosting)

1. Import the GitHub repo in Vercel. Root directory: `instantgrill-web` if it lives in a monorepo.
2. Environment variables (see `.env.example`):
   - `DATABASE_URL` from step 1
   - `ADMIN_EMAIL`, `ADMIN_PASSWORD` (12+ chars), `ADMIN_SESSION_SECRET` (`openssl rand -hex 32`)
   - `RESEND_API_KEY`, `OWNER_EMAIL` (order alerts by email, verify instantgrill.shop in Resend)
   - `NEXT_PUBLIC_META_PIXEL_ID`, `META_CAPI_TOKEN`, `NEXT_PUBLIC_GA4_ID`
3. Deploy. Open `/admin`, sign in, and in **Menu & areas** set the delivery areas and fees.

## 3. TechDeva dashboard

On the platform, sign in as the Instant Grill owner, open `/orders-view` and press
**Connect website**. Put the two values it shows into Vercel as `PLATFORM_WEBHOOK_URL`
and `PLATFORM_WEBHOOK_SECRET`, redeploy, then press **Sync to TechDeva** in `/admin`.
From then on every order, status change, enquiry and message appears on the platform
live. Details: `docs/STOREFRONT.md` in the platform repo.

## 4. Domain

Vercel > Project > Domains > add `instantgrill.shop` and `www.instantgrill.shop`, then set the DNS
records Vercel shows at your domain registrar. Keep the old site live until the new one checks out.

## 5. Photos and video

Put originals in `assets-original/photos` (large), `assets-original/cards` (menu cards),
`assets-original/unboxing` (the ezgif frames) and `assets-original/logo.png|svg`, then run
`npm run media`. It writes light WebP files and one unboxing video, and prints the weight saved.
Set each slot's `src` in `src/lib/catalog.ts` (and `media.unboxing` for the video).

## 6. Check before switching DNS

- Place a test order on a phone: it appears in `/admin/orders` and on the platform's `/orders-view`,
  the email arrives, WhatsApp opens with the order.
- Meta Events Manager > Test events (`META_CAPI_TEST_CODE`): Purchase arrives from browser and server, deduplicated.
- Run Lighthouse (mobile) on the home and menu pages.
