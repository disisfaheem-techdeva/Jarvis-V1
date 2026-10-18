# Storefront orders (website → platform)

A tenant's own website sends its orders, catering enquiries and contact messages
to the platform. The owner sees them on **/orders-view** (also `/storefront-view`):
upcoming deliveries by slot, the order pipeline, 14-day revenue, top items, areas,
marketing sources (utm), repeat customers, and the enquiry inbox.

Generic module: Instant Grill is the first tenant to use it; any client with a
website can connect the same way. Code: `app/storefront.py`, `web/storefront.html`,
`db/29_storefront_orders.sql`. Tests: `tests/test_storefront.py`.

## How it works

```
instantgrill.shop ──HMAC-signed POST──► /storefront/webhook/{tenant_id}
   order.created / order.updated / enquiry.created / contact.created
        │
        ▼
storefront_orders, storefront_inbox  (RLS: tenant_isolation, FORCED)
        │
        ├─ revenue_events row once, when an order becomes delivered (COD: delivered = paid)
        └─ events: storefront.order.created, storefront.order.status_changed,
                   storefront.enquiry.created, storefront.contact.created
                   → rules 21-23 in app/orchestrator/rules.py (owner alerts)
```

- **Signature:** `X-Storefront-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>.<body>")>`.
  Older than 5 minutes is refused, so captured requests cannot be replayed.
- **Secret:** stored in `integrations` (provider `storefront`), encrypted with
  `app/crypto.py`. Shown once when the owner connects; connecting again rotates it.
- **Safe retries:** orders are upserted by the site's order number, and an update
  older than the stored one is ignored, so retries, re-syncs and out-of-order
  delivery never duplicate or roll back an order. Revenue is recorded exactly once.
- **Unknown tenant and bad signature look the same** (401), so nothing can be probed.
- **Menu → Products (`catalog.updated`):** the site sends its whole menu (catalog +
  the owner's price, sold-out and hidden overrides) after every edit in `/admin/menu`
  and first thing on "Sync to TechDeva". The platform keeps the business's Products
  in step, keyed by `sku = 'site:<slug>'`: new items are added, names and prices
  follow, a product the owner typed in by hand with the same name is adopted, and
  an item hidden or gone from the menu is switched off (never deleted, old orders
  point at it). Order lines then link to these products by name.

## Going live for Instant Grill

1. On the VPS database (as the migration role, like the other `db/*.sql` files):
   ```
   psql ... -f db/29_storefront_orders.sql
   psql ... -f db/30_seed_instant_grill.sql
   ```
   Both are idempotent. The seed creates the tenant `33333333-3333-3333-3333-333333333333`,
   its config row and the owner `info@instantgrill.shop`.
2. Give the owner a login:
   `docker compose exec -T api python scripts/issue_setup_link.py info@instantgrill.shop`
3. Owner signs in, opens **/orders-view**, enters the site address and presses
   **Connect website**. Copy the webhook URL and the secret shown.
4. On the website host (Vercel), set `PLATFORM_WEBHOOK_URL` and
   `PLATFORM_WEBHOOK_SECRET`, redeploy, then press **Sync to TechDeva** once in the
   site admin to send past orders.

## Hooking Hermes and Meta in

Faheem connects these himself. The platform events above are the hooks: add a
`queue_task` for Hermes to the `storefront.order.created` rule (order confirmation
on WhatsApp) or to `storefront.order.status_changed` with `status == "out_for_delivery"`
(rider on the way). Orders carry `source.utmSource` / `utmCampaign` for attribution
once Meta spend is connected.
