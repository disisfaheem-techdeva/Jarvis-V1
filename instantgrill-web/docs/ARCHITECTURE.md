# Instant Grill: architecture

## Ecosystem

```
 Meta / TikTok / Google ads  ──utm──►  instantgrill.shop (Next.js 16)
                                         │
             ┌───────────────────────────┼─────────────────────────────┐
             │ Storefront (static)       │ API (route handlers)        │
             │  /            home        │  POST /api/orders           │
             │  /checkout    COD form    │  GET  /api/orders/:id?phone │
             │  /order/:id   thank you   │  GET  /api/admin/orders     │
             │  /track       lookup      │  PATCH /api/admin/orders/:id│
             │  /admin       ops         │                             │
             └───────────────────────────┴──────────────┬──────────────┘
                                                        │
                       OrderRepository ◄────────────────┤
                        ├─ PostgresOrderRepository  (DATABASE_URL set)
                        └─ FileOrderRepository      (dev / single server)
                                                        │
                       ORDER_WEBHOOK_URL ◄──────────────┘  order.created
                        ├─ Google Sheet (Apps Script)
                        ├─ WhatsApp agent / team alert
                        └─ courier booking (next step)

 Customer ──wa.me──► WhatsApp: confirm order (cuts COD returns)
 Team     ──/admin─► confirm, ship (courier + tracking no.), deliver, return
```

## Code map

| Path | Role |
|---|---|
| `src/lib/catalog.ts` | Single source of truth: prices, bundles, claims, FAQ, image slots. Edit `EDIT` values. |
| `src/lib/pricing.ts` | Server-side cart pricing, free-delivery threshold |
| `src/lib/validation.ts` | Order validation, Pakistani mobile normalisation |
| `src/lib/orders.ts` | Place order, public view, webhook, admin auth, rate limit |
| `src/lib/store/*` | Repository interface, Postgres and file implementations, status rules |
| `src/components/home/*` | One file per home section |
| `src/components/MotionImage.tsx` | Animated image (reveal, settle, parallax) used for every photo |
| `src/components/ImageSlot.tsx` | Renders a photo or a labelled placeholder until it exists |
| `db/schema.sql` | Postgres table and indexes |
| `tests/*` | Vitest unit tests |

## Data model

`Order { id, createdAt, status, customer{name, phone, city, address, notes}, lines[], subtotal, shipping, total, paymentMethod: "cod", courier?, trackingNumber?, history[{status, at, note}], source{utmSource, utmCampaign, referrer} }`

Status flow:

```
pending ─► confirmed ─► shipped ─► delivered
   │           │            └────► returned (RTO)
   └───────────┴─► cancelled
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
