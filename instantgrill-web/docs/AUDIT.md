# Instant Grill: build notes and launch checklist

## Corrections made (from the owner's brief)

- **The live site is up.** An earlier note said instantgrill.shop did not load. That was wrong:
  the build machine's network cannot reach outside sites. Do not change DNS.
- **The business is a ready-to-grill BBQ box delivered in Lahore**, not a grill sold by courier.
  The first draft's kits, prices, courier shipping and replacement policy were removed.
- The menu, prices, contact details and delivery rules now come from the owner, in `src/lib/catalog.ts`.

## What the site does

- Home: hero, promises, why us (photo bento), bestsellers, how it works, what's in every box, deals, FAQ.
- `/menu`: sticky category tabs that follow the scroll, Boneless / Kabab / Premium filters, search,
  quick add (boxes ask for chutney and salad inline), floating cart bar on phones.
- `/menu/[slug]`: box builder with required chutney and salad, extra skewers, drinks, quantity, live total,
  "what's in the box", order on WhatsApp, related boxes, Product JSON-LD.
- Cart drawer with drink and skewer upsells.
- `/checkout`: name, phone (PK format), Lahore area, address, landmark, delivery day and time from the
  slot rules, COD (JazzCash and Easypaisa shown as coming soon), double-submit guard.
- `/order/[id]`: confirmation, delivery time, "Send to WhatsApp" with the full order, grilling guide.
- `/track`: order number + phone (phone must match), status timeline.
- `/admin`: orders with New, Confirmed, Preparing, Out for delivery, Delivered or Cancelled; rider name;
  delivery time per order; "due today" and "to confirm" counts; WhatsApp the customer.

## Delivery rules (server-validated, `src/lib/delivery.ts`)

- Orders 12 PM to 5:30 PM: earliest slot 6 hours later, rounded up to 30 minutes; last slot 11:30 PM.
- Orders after 5:30 PM: next morning, 10 AM to 12 PM.
- Orders 12 AM to 10 AM: that morning, 10 AM to 12 PM.
- Orders 10 AM to 12 PM (before opening): 6 hours counted from 12 PM. **Assumption, confirm.**
- Customer can pick any later slot up to 7 days ahead. A full day = morning window + every 30 min from 12:30 PM.

## QA (run on this build)

- [x] 65 unit tests: every menu price, deal savings, chutney/salad rules, server pricing, all slot rules
      (including Lahore vs UTC day boundary), validation, order status flow, file store under concurrency
- [x] TypeScript strict, ESLint, production build
- [x] Browser run at 375px: home, menu, builder (total Rs 3,850 correct), checkout errors, order placed,
      WhatsApp message contains the full order, slot "today 7 PM" correct for a 12:55 PM order
- [x] Admin walked an order New to Out for delivery; tracking shows it; wrong phone shows nothing
- [x] No horizontal scroll at 375px and 1440px; no console errors

## Added in the third pass

- Pages: `/deals` (family, Dawat, Party with savings from live prices + catering enquiry form),
  `/how-it-works` (4 steps, safety, storage, video slot), `/about` (story, values, map),
  `/faq` (accordion + FAQPage JSON-LD), `/contact` (WhatsApp, form, map, Restaurant JSON-LD),
  `/privacy`, `/terms`. Header and footer link to all of them.
- Owner admin with email + password login (signed httpOnly cookie, same-origin check on writes,
  login rate limit): Dashboard, Orders (status/date filters, rider, printable kitchen slip),
  Menu & areas (price, sold out, hide; per-area delivery fees), Customers, Enquiries.
- Owner edits apply everywhere at once: menu, product pages, cart, checkout and server pricing.
- Supabase-ready Postgres store and migration with row-level security (verified: the `anon`
  role is denied); file store for local dev.
- Owner alerts: email via Resend and an optional webhook, for orders, enquiries and messages.
- Meta Pixel + Conversions API (Purchase deduplicated by order number, phone hashed) and GA4.
- Share images (site and per product), Roman Urdu keywords, full sitemap.
- `npm run media`: photos to WebP, unboxing frames to one MP4/WebM under 2 MB, logo under 30 KB.

## QA, third pass

- [x] 79 unit tests + 3 Postgres integration tests against a real Postgres 16
- [x] Browser run, 31 checks, on both file storage and Postgres: every page 200 with no sideways
      scroll at 375px; wrong password refused; price edit and sold-out show on the menu; Gulberg fee
      Rs 200 added (server total Rs 3,350); order walked to Preparing with rider; slip prints items,
      choices and rider; dashboard revenue; enquiry and contact forms land in admin; cross-site write
      with a valid owner cookie refused; admin API refused without a session; share image renders
- [x] Fixed during QA: admin switch knob drawn outside its track; WhatsApp button covering admin controls

## [TO CONFIRM] before launch

1. Delivery areas and fees: set them in `/admin` > Menu & areas. Empty fee = confirmed on WhatsApp.
2. Kitchen map coordinates for the JSON-LD `geo` field, and the About page story wording.
3. Facebook Page URL (`brand.facebook`).
4. Drink brands and flavours.
5. Orders placed 10 AM to 12 PM: is earliest 6 PM right?
6. Real photos (see `docs/IMAGE_BRIEF.md`). Check AI images for wrong text on the box before use.

## Next

- See `docs/LAUNCH.md` for Supabase, Vercel, domain, photos and the pre-launch check.
- Live order status on the tracking page without refresh (Supabase Realtime), if wanted.
