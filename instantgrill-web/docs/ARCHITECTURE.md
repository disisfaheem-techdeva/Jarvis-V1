# Instant Grill: architecture

## Ecosystem

```
 Instagram / TikTok / Meta ads ──utm──► instantgrill.shop (Next.js 16)
                                         │
             ┌───────────────────────────┼─────────────────────────────┐
             │ Storefront (static)       │ API (route handlers)        │
             │  /            home        │  POST /api/orders           │
             │  /menu        tabs        │                             │
             │  /menu/:slug  box builder │                             │
             │  /checkout    COD + slot  │  GET  /api/orders/:id?phone │
             │  /order/:id   thank you   │  GET  /api/admin/orders     │
             │  /track       lookup      │  PATCH /api/admin/orders/:id│
             │  /deals       + enquiry   │  POST /api/enquiries        │
             │  /contact     + form      │  POST /api/contact          │
             │  /about /faq /how-it-works│  POST /api/admin/login      │
             │  /admin/*     owner panel │  PATCH /api/admin/menu/:slug│
             │                           │  PUT  /api/admin/areas      │
             └───────────────────────────┴──────────────┬──────────────┘
                                                        │
                 Store { orders, enquiries, messages, settings }
                        ├─ Postgres / Supabase  (DATABASE_URL set, RLS on)
                        └─ JSON files           (local dev)
                                                        │
                 notify ◄───────────────────────────────┤  order / enquiry / contact
                        ├─ Resend email to OWNER_EMAIL
                        └─ ORDER_WEBHOOK_URL (Sheet, Zapier, WhatsApp agent)
                 capi   ◄───────────────────────────────┤  Meta Conversions API Purchase
                 platform ◄─────────────────────────────┘  signed events to TechDeva /storefront/webhook
                          (orders, status changes, enquiries, messages; "Sync to TechDeva" resends all)

 Customer ──wa.me──► WhatsApp 0335 4693665: full order prefilled
 Owner    ──/admin─► confirm, preparing, out for delivery (rider), delivered
```

## Code map

| Path | Role |
|---|---|
| `src/lib/catalog.ts` | Base menu, prices, contact, default areas, FAQ, image slots |
| `src/lib/menu.ts` | Live menu = catalog + owner edits (prices, sold out, hidden, areas) from the store |
| `src/lib/auth.ts` | Owner login: env credentials, signed session cookie, same-origin check |
| `src/lib/notify.ts`, `src/lib/capi.ts`, `src/lib/analytics.ts` | Owner alerts, Meta CAPI, Pixel + GA4 events |
| `src/lib/platform.ts` | Signed delivery of events to the TechDeva platform, with retries |
| `src/lib/admin-stats.ts` | Dashboard numbers and customers, derived from orders |
| `src/lib/delivery.ts` | Delivery slot rules (Asia/Karachi), shared by checkout and the API |
| `src/lib/pricing.ts` | Server-side cart pricing with chutney/salad rules |
| `src/lib/validation.ts` | Order validation, Pakistani mobile normalisation |
| `src/lib/orders.ts` | Place order, public view, rate limit |
| `src/lib/store/*` | Repository interface, Postgres and file implementations, status rules |
| `src/components/home/*` | One file per home section |
| `src/components/MotionImage.tsx` | Animated image (reveal, settle, parallax) used for every photo |
| `src/components/ImageSlot.tsx` | Renders a photo or a labelled placeholder until it exists |
| `db/migrations/001_init.sql` | Supabase/Postgres schema, customers view, RLS |
| `scripts/optimize-media.mjs` | `npm run media`: WebP photos, unboxing video, logo |
| `tests/*` | Vitest unit tests |

## Data model

`Order { id, createdAt, status, customer{name, phone, area, address, landmark, notes}, lines[{slug, name, unitPrice, qty, chutney, salad}], subtotal, deliveryFee|null, total, slot{date, slot}, paymentMethod: "cod", rider?, history[{status, at, note}], source{utmSource, utmCampaign, referrer} }`

Status flow:

```
new ─► confirmed ─► preparing ─► out_for_delivery ─► delivered
 └──────────┴────────────┴────────────────┴─► cancelled
```

## Design system

- Theme: dark-locked (fire on charcoal). Tokens in `src/app/globals.css` under `@theme`.
- Accent: ember `#f2652a` for actions and prices only.
- Type: Geist Sans + Geist Mono (numbers).
- Shape: cards 20px, inputs 12px, buttons full pill.
- Motion: Motion (`motion/react`). `MotionConfig reducedMotion="user"`; every component renders
  identical initial props on server and client.

## Run

```bash
cd instantgrill-web
cp .env.example .env.local   # set ADMIN_TOKEN
npm install
npm run dev                  # http://localhost:3000
npm test && npm run typecheck && npm run lint && npm run build
```

Deploy: Vercel (set env vars, use Postgres such as Neon or Supabase), or any Node host via
`output: "standalone"` (`node .next/standalone/server.js`). The file store needs a persistent
disk and a single instance; use Postgres otherwise.
