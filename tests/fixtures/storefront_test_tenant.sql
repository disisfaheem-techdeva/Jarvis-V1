-- TEST FIXTURE ONLY. Never apply to production: it creates a test tenant
-- (33333333-...) that tests/test_storefront.py posts orders to. It lives here, not in
-- db/, so deploy scripts that run every db/*.sql can never pick it up.
-- ============================================================================
-- 30_seed_instant_grill.sql · Tenant: Instant Grill (food delivery, Lahore)
--
-- Onboarding by configuration only (no code): one tenants row, one
-- tenant_config row, one owner. The website connects through the storefront
-- module (db/29); Hermes and Meta are connected by the owner from the portal.
-- Idempotent: safe to run again.
-- ============================================================================
BEGIN;

INSERT INTO tenants (id, name, industry, status, plan, billing)
VALUES ('33333333-3333-3333-3333-333333333333', 'Instant Grill', 'food_delivery', 'active', 'subscription', 'monthly')
ON CONFLICT (id) DO NOTHING;

INSERT INTO tenant_config (tenant_id, services, price_list, departments, booking_rules,
                           hermes_persona, brand, geo, working_hours, automation)
VALUES (
  '33333333-3333-3333-3333-333333333333',
  -- The website's catalog is the source of truth for live prices; this mirrors it for agents.
  '[{"name":"Chicken Tikka","department":"chicken","price":3000},
    {"name":"Chicken Kabab (6 pc)","department":"chicken","price":3100},
    {"name":"Chicken Shashlik","department":"chicken","price":3200},
    {"name":"Malai Boti","department":"chicken","price":3300},
    {"name":"Beef Seekh Kabab","department":"beef","price":3500},
    {"name":"Beef Bihari Boti","department":"beef","price":3800},
    {"name":"Rib Eye Tikka","department":"beef","price":3900},
    {"name":"Mutton Seekh Kabab","department":"mutton","price":4200},
    {"name":"Mutton Reshmi Boti","department":"mutton","price":4800},
    {"name":"Mutton Namkeen Boti","department":"mutton","price":4800},
    {"name":"Mutton Champ (6 pc)","department":"mutton","price":4800},
    {"name":"Trio Mix","department":"mix","price":3600},
    {"name":"Family Chicken","department":"family","price":6000},
    {"name":"Family Mutton","department":"family","price":8500},
    {"name":"Dawat Deal (serves 6)","department":"deals","price":10000},
    {"name":"Party Deal (serves 8)","department":"deals","price":12500}]',
  '{"currency":"PKR","website":"https://instantgrill.shop/menu"}',
  '["chicken","beef","mutton","mix","family","deals"]',
  '{"delivery":"6 hours after order; after 5:30 PM or 12 AM-10 AM -> next morning 10 AM-12 PM","payment":"cash on delivery"}',
  NULL,   -- Hermes persona: set by the owner when Hermes is connected
  '{"colors":{"primary":"#F2652A","background":"#0C0D0F"},"positioning":"Pakistan''s first ready-to-grill BBQ box"}',
  '{"city":"Lahore","area":"Baghbanpura","address":"Orange Square, Baghbanpura, near UET, Lahore"}',
  '{"daily":"12:00-24:00","morning_delivery":"10:00-12:00","timezone":"Asia/Karachi"}',
  'assisted'
)
ON CONFLICT (tenant_id) DO NOTHING;

-- Owner login is set up through the existing first-password flow (no password here).
INSERT INTO users (tenant_id, name, email, phone, role)
VALUES ('33333333-3333-3333-3333-333333333333', 'Instant Grill Owner', 'info@instantgrill.shop', '+923354693665', 'owner')
ON CONFLICT (email) DO NOTHING;

COMMIT;
