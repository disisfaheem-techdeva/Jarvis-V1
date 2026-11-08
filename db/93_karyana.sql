-- 93: Karyana (grocery) stores, a business type of their own (8 Nov 2026)
--
-- First industry of the Jarvis plan (ADR-006 in the TechDeva docs repo). A
-- karyana sells products like a shop does, but by weight, volume and piece, and
-- its shopkeeper and customers name things in Urdu or Roman Urdu ("cheeni",
-- "aata", "ghee"). So:
--
--   business_categories  one row, 'karyana'. The app treats it as a business
--                        that sells products (app/categories.PRODUCT_TYPES).
--   products.unit        how the product is sold: piece, kg, g, litre, ml,
--                        dozen, pack. NULL = not said (every existing product).
--   products.aliases     other names the product goes by. Matching a spoken or
--                        typed name to a product tries the name first, then an
--                        alias exactly, and never guesses between two products
--                        (app/orders.match_item). Empty = matching as before.
--
-- Additive: one row and two columns with safe defaults; no existing value
-- changes. Rollback:
--   UPDATE tenants SET business_type = NULL WHERE business_type = 'karyana';
--   DELETE FROM business_categories WHERE slug = 'karyana';
--   ALTER TABLE products DROP COLUMN aliases, DROP COLUMN unit;
BEGIN;

ALTER TABLE products ADD COLUMN IF NOT EXISTS unit text
  CHECK (unit IS NULL OR unit IN ('piece','kg','g','litre','ml','dozen','pack'));
ALTER TABLE products ADD COLUMN IF NOT EXISTS aliases text[] NOT NULL DEFAULT '{}'
  CHECK (cardinality(aliases) <= 20
         AND array_position(aliases, NULL) IS NULL
         AND length(array_to_string(aliases, '')) <= 1000);

INSERT INTO business_categories (slug, name, blurb, icon, sort_order, labels, dashboard_widgets, pipeline_stages, agent_menu, agent_rules, intake_fields) VALUES
('karyana', 'Karyana / grocery store', 'Kiryana, general store, grocery: sold by kg, litre and piece, with udhaar customers', '🛒', 61,
 '{"service":"Product","services":"Products","booking":"Order","bookings":"Orders","customer":"Customer","customers":"Customers"}',
 '["sales_today","orders_new","orders_waiting","customers","whatsapp_chats","new_leads"]',
 '{"new":"Inquiry","qualified":"Interested","booked":"Order placed","showed":"Delivered","sold":"Regular customer","lost":"Lost","fake":"Spam"}',
 '["Our items and rates","Place an order","Home delivery","Shop location"]',
 'You serve a karyana (grocery) store. Talk only about the items below, at the prices below, in the unit each is sold by (kg, litre, piece, pack, dozen). Customers may name items in Urdu or Roman Urdu (cheeni, aata, chawal, ghee, daal): match them to the listed items, and if you are not sure which item they mean, ask; never guess. Customers ORDER items: never ask for an appointment. To take an order, ask for each item (from the list only) with its quantity and unit, the customer''s name, and whether they want home delivery (then the full address) or will pick it up. Then send the order summary exactly as described under "Taking an order". If an item is not listed, say plainly the shop does not have it. Never promise stock, a delivery time or credit (udhaar) you have not been told about; say the shopkeeper will confirm.',
 '["Items with prices and the unit each is sold by (kg, litre, piece, pack)","Other names customers use for items (Urdu or Roman Urdu)","Home delivery: areas, charge, how long it takes","Pickup: yes or no","Payment methods (cash, JazzCash, EasyPaisa, bank)","Udhaar: do you give credit, and to whom","Opening hours","Location","What you do NOT sell"]')
ON CONFLICT (slug) DO NOTHING;

DO $verify$
BEGIN
  IF (SELECT count(*) FROM information_schema.columns WHERE table_name = 'products'
        AND column_name IN ('unit','aliases')) <> 2 THEN
    RAISE EXCEPTION 'MIGRATION INCOMPLETE - products.unit / products.aliases missing';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM business_categories WHERE slug = 'karyana') THEN
    RAISE EXCEPTION 'MIGRATION INCOMPLETE - karyana business type missing';
  END IF;
  RAISE NOTICE 'OK: karyana business type; products can carry a unit and aliases.';
END
$verify$;
COMMIT;
