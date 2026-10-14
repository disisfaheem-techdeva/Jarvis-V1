# Instant Grill: website audit and target spec

Reviewers: CTO, web designer, QA, front-end and back-end (one pass, five hats).

## 0. Finding zero: the site did not load

From the cloud test environment, `instantgrill.shop` and `www.instantgrill.shop` returned
**"domain not found" (DNS ENOTFOUND)**. The reference `instagrill.shop` returned the same.
Check this first, from a phone on mobile data:

- Is the domain still registered and paid for?
- Do its DNS records (A / CNAME) point at the host (Shopify, Vercel, etc.)?
- Is there an `A` or `CNAME` for both the bare domain and `www`?

If customers see the same error, every rupee of ad spend is landing on nothing. Fix this
before anything below.

Because the live site could not be opened, this audit covers why typical template stores
"look basic" and lists what this rebuild does instead. Send screenshots of the current
site for a line-by-line review.

## 1. Why template stores look basic (and what changes)

| Problem on most template stores | Why it hurts | What this build does |
|---|---|---|
| Stock or supplier photos, mixed lighting | Looks like every other reseller, so trust drops | One art-directed photo set, dark and warm, briefs in `docs/IMAGE_BRIEF.md` |
| Three identical icon cards for "Why choose us" | Reads as filler, nobody remembers it | Five-cell photo bento grid with different sizes, each tile a real benefit |
| Static page, no motion | Feels cheap next to brand sites | Images clip-reveal, settle and drift with scroll; hero glow flickers like coals |
| Default theme fonts and colours | No brand identity | Geist type, coal-black base, one ember-orange accent used only for actions |
| Generic checkout, long forms, card-first | Pakistan buys COD; card-first forms leak orders | 5-field COD checkout, PK phone validation, city picker, WhatsApp confirm |
| No order follow-up | High return-to-origin (RTO) on COD | Confirm-on-WhatsApp button after order + admin "Message" button per order |
| Fake reviews or countdown timers | Customers spot them; Meta and Google can penalise them | Reviews section only renders real reviews; no fake urgency |
| Slow pages (apps, sliders, popups) | Mobile users on 4G leave | Static pages, next/image, one font family, no third-party apps |

## 2. Design review

- **Direction:** premium outdoor-cooking brand, not a gadget reseller. Dark, cinematic, warm.
- **Not like the reference:** no light layout, no generic product-card grid, no copy of their sections.
- **Hierarchy:** one message per section. Max one accent colour. Buttons are pills, cards are 20px.
- **Mobile first:** sticky bottom "Order now" bar, thumb-sized controls, WhatsApp button above it.

## 3. QA checklist (all checked on this build)

- [x] Production build passes, TypeScript strict, ESLint clean
- [x] 22 unit tests: pricing, validation, order status rules, file store under concurrency
- [x] End-to-end in Chromium: add to cart, checkout errors, place order, track, admin confirm/ship
- [x] No horizontal scroll at 1440px and 390px
- [x] Reduced motion: content visible, animations reduced (a hydration bug here was found and fixed)
- [x] Server ignores client prices (tested by posting a fake price)
- [x] Rate limit counts only valid orders (bug found and fixed: typos were locking customers out)
- [ ] Real photos in place (see image brief)
- [ ] Real prices, WhatsApp number, policy text (all marked `EDIT` in `src/lib/catalog.ts`)
- [ ] Lighthouse run on the deployed domain

## 4. Back-end review

- Prices always computed on the server from the catalog.
- Order lookup needs order ID **and** phone, so IDs cannot be enumerated.
- Admin API needs a 16+ character bearer token, compared in constant time; off when unset.
- Honeypot field and per-IP limit on orders; put Cloudflare in front for real abuse.
- Webhook on new orders is fire-and-forget with a 5s timeout: it can never break checkout.
- Order status moves only along allowed paths (pending, confirmed, shipped, delivered; or cancelled/returned).

## 5. Next upgrades, in order

1. Photos (the biggest visual jump). Then replace `src: null` in the catalog.
2. Point `ORDER_WEBHOOK_URL` at a Google Sheet or WhatsApp agent so every order alerts the team.
3. Meta Pixel + Conversions API purchase event on `/order/[id]` (needed to optimise ads).
4. Courier API booking (PostEx, Leopards, TCS) from the admin "Mark shipped" button.
5. Prepaid option (JazzCash / Easypaisa) with a small discount, to cut COD returns.
6. Real reviews with photos after the first 50 deliveries.
