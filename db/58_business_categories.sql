-- 58_business_categories.sql
-- Business type ("category") as DATA, so one dashboard and one WhatsApp agent
-- fit any service business (Faheem, 4 Oct 2026). Found: the WhatsApp agent was
-- given only the business NAME, never its real services, prices or hours, so it
-- guessed (a lead was offered services the business does not have), and the
-- template's wording assumed a clinic. And nothing read what the live agents
-- actually said.
--
--   business_categories   one row per business type: dashboard tiles, words,
--                         lead stage names, the WhatsApp menu, the agent's rules
--                         for that type, and what the owner must tell us.
--                         Adding a type = inserting a row. No code change.
--   tenants.business_type the type the owner picked (onboarding gate)
--   business_knowledge    per business: FAQs, policies, things NOT offered
--   agent_knowledge_sync  what was last written into the agent, and when
--   agent_reply_flags     the reply checker's findings (bot errors, services or
--                         prices the business does not have)
--   agent_reply_scan      how far the reply checker has read, per business
-- Additive only.
BEGIN;

CREATE TABLE IF NOT EXISTS business_categories (
  slug               text PRIMARY KEY CHECK (slug ~ '^[a-z][a-z0-9_]{1,30}$'),
  name               text NOT NULL,
  blurb              text NOT NULL DEFAULT '',
  icon               text NOT NULL DEFAULT '',
  sort_order         int  NOT NULL DEFAULT 100,
  active             boolean NOT NULL DEFAULT true,
  labels             jsonb NOT NULL DEFAULT '{}',   -- {service, services, booking, bookings, customer, customers}
  dashboard_widgets  jsonb NOT NULL DEFAULT '[]',   -- tile keys the dashboard knows
  pipeline_stages    jsonb NOT NULL DEFAULT '{}',   -- lead_status -> name shown
  agent_menu         jsonb NOT NULL DEFAULT '[]',   -- WhatsApp quick menu
  agent_rules        text  NOT NULL DEFAULT '',     -- rules for this type of business
  intake_fields      jsonb NOT NULL DEFAULT '[]',   -- what the owner must tell us
  updated_at         timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE business_categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE business_categories FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS read_all ON business_categories;
CREATE POLICY read_all ON business_categories FOR SELECT USING (true);
DROP POLICY IF EXISTS admin_write ON business_categories;
CREATE POLICY admin_write ON business_categories FOR ALL USING (is_admin()) WITH CHECK (is_admin());
GRANT SELECT, INSERT, UPDATE ON business_categories TO techdeva_app;

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS business_type text REFERENCES business_categories(slug);
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS business_type_other text;

CREATE TABLE IF NOT EXISTS business_knowledge (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  kind        text NOT NULL CHECK (kind IN ('faq','policy','not_offered','info')),
  title       text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 300),
  body        text NOT NULL DEFAULT '' CHECK (length(body) <= 2000),
  active      boolean NOT NULL DEFAULT true,
  created_by  uuid,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS business_knowledge_tenant ON business_knowledge (tenant_id, active, kind);

CREATE TABLE IF NOT EXISTS agent_knowledge_sync (
  tenant_id   uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  hash        text,
  status      text NOT NULL DEFAULT 'never',
  detail      text,
  synced_at   timestamptz
);

CREATE TABLE IF NOT EXISTS agent_reply_flags (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  agent_key   text NOT NULL,
  session_id  text NOT NULL,
  msg_hash    text NOT NULL,
  message_at  timestamptz,
  kind        text NOT NULL CHECK (kind IN ('bot_error','unknown_service','unbacked_claim')),
  excerpt     text NOT NULL,
  detail      text NOT NULL DEFAULT '',
  status      text NOT NULL DEFAULT 'open' CHECK (status IN ('open','dismissed')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, msg_hash, kind)
);
CREATE INDEX IF NOT EXISTS agent_reply_flags_open ON agent_reply_flags (tenant_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_reply_scan (
  tenant_id        uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  last_message_at  timestamptz,
  scanned_at       timestamptz
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['business_knowledge','agent_knowledge_sync','agent_reply_flags','agent_reply_scan'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (is_admin() OR tenant_id = current_tenant()) WITH CHECK (is_admin() OR tenant_id = current_tenant())', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO techdeva_app', t);
  END LOOP;
END $$;

-- ---- the first six types (more are rows, added any time) ----
INSERT INTO business_categories (slug, name, blurb, icon, sort_order, labels, dashboard_widgets, pipeline_stages, agent_menu, agent_rules, intake_fields) VALUES
('restaurant', 'Restaurant, café or food', 'Dine-in, takeaway, delivery, catering', '🍽️', 10,
 '{"service":"Menu item","services":"Menu","booking":"Reservation","bookings":"Reservations","customer":"Guest","customers":"Guests"}',
 '["bookings_today","new_leads","whatsapp_chats","revenue_today","services_count","customers"]',
 '{"new":"Inquiry","qualified":"Interested","booked":"Reservation made","showed":"Seated / delivered","sold":"Repeat guest","lost":"Lost","fake":"Spam"}',
 '["See the menu","Book a table","Order for delivery","Opening hours","Location"]',
 'You serve a restaurant. Talk only about dishes and drinks on the menu below, at the prices below. To book a table, ask for the date, time, number of people and a name, then say the team will confirm. To take a delivery order, ask for the items (from the menu only), quantities and the full address, add up the total from the menu prices, and say the team will confirm the time. If someone asks for a dish, drink or service that is not on the menu or in the facts below (for example catering, alcohol or a dish not listed), say clearly that the restaurant does not offer it and suggest something that is on the menu. For allergies or ingredients you have not been told, say the team will check and reply.',
 '["Menu: every dish and drink with its price","Opening hours","Table booking: yes or no, and the largest group","Delivery: area, minimum order, delivery charge","Location or Google Maps link","What you do NOT offer (for example catering, alcohol)"]'),
('clinic', 'Clinic (aesthetic, dental, medical)', 'Treatments, consultations, procedures', '🩺', 20,
 '{"service":"Treatment","services":"Treatments","booking":"Appointment","bookings":"Appointments","customer":"Patient","customers":"Patients"}',
 '["bookings_today","new_leads","whatsapp_chats","revenue_today","services_count","customers"]',
 '{"new":"Lead","qualified":"Consultation","booked":"Booked","showed":"Treated","sold":"Returning patient","lost":"Lost","fake":"Spam"}',
 '["Treatments and prices","Book a consultation","Timings","Location","Talk to the team"]',
 'You serve a clinic. Talk only about the treatments below, at the prices below. Never diagnose, never promise results, never give medical advice beyond what the facts below say; offer a consultation instead. For an emergency, tell the person to go to the nearest hospital now. To book, ask for the treatment, a preferred date and time, and a name, then say the team will confirm. If asked for a treatment that is not listed, say the clinic does not offer it.',
 '["Treatments with prices (or \"price after consultation\")","Consultation fee","Opening hours","Doctors or specialists","Location","What you do NOT offer"]'),
('salon', 'Salon, barber or spa', 'Hair, beauty, grooming, spa', '💇', 30,
 '{"service":"Service","services":"Services","booking":"Appointment","bookings":"Appointments","customer":"Client","customers":"Clients"}',
 '["bookings_today","new_leads","whatsapp_chats","revenue_today","services_count","customers"]',
 '{"new":"Inquiry","qualified":"Interested","booked":"Booked","showed":"Visited","sold":"Regular client","lost":"Lost","fake":"Spam"}',
 '["Services and prices","Book an appointment","Timings","Location"]',
 'You serve a salon. Talk only about the services below, at the prices below. To book, ask for the service, a preferred date and time, any stylist preference, and a name, then say the team will confirm. If asked for a service that is not listed, say the salon does not offer it and suggest a listed one.',
 '["Services with prices","Opening hours","Stylists (optional)","Walk-ins or booking only","Location","What you do NOT offer"]'),
('gym', 'Gym or fitness studio', 'Memberships, classes, personal training', '🏋️', 40,
 '{"service":"Plan","services":"Plans","booking":"Session","bookings":"Sessions","customer":"Member","customers":"Members"}',
 '["bookings_today","new_leads","whatsapp_chats","revenue_today","services_count","customers"]',
 '{"new":"Inquiry","qualified":"Interested","booked":"Trial booked","showed":"Trial done","sold":"Member","lost":"Lost","fake":"Spam"}',
 '["Membership plans","Book a free trial","Class timings","Location"]',
 'You serve a gym. Talk only about the plans, classes and prices below. Never give medical or diet advice and never promise weight loss or results. To book a trial, ask for a preferred day and time and a name, then say the team will confirm. If asked for something not listed, say the gym does not offer it.',
 '["Membership plans with prices","Classes and timings","Opening hours (men / women if separate)","Free trial: yes or no","Location","What you do NOT offer"]'),
('academy', 'Academy, training or school', 'Courses, batches, tuition', '🎓', 50,
 '{"service":"Course","services":"Courses","booking":"Enrolment","bookings":"Enrolments","customer":"Student","customers":"Students"}',
 '["bookings_today","new_leads","whatsapp_chats","revenue_today","services_count","customers"]',
 '{"new":"Inquiry","qualified":"Interested","booked":"Enrolment booked","showed":"Attending","sold":"Enrolled","lost":"Lost","fake":"Spam"}',
 '["Courses and fees","Next batch dates","Enrol","Location"]',
 'You serve an academy. Talk only about the courses, fees and batch dates below. Never promise jobs, results or certificates unless the facts below say so. To enrol, ask for the course, the student''s name and a phone number, then say the team will confirm. If asked for a course that is not listed, say the academy does not offer it.',
 '["Courses with fees and duration","Next batch dates","Certificate given (yes or no)","Online, on-site or both","Location","What you do NOT offer"]'),
('other', 'Other service business', 'Anything else: tell us what you do', '✨', 90,
 '{"service":"Service","services":"Services","booking":"Booking","bookings":"Bookings","customer":"Customer","customers":"Customers"}',
 '["bookings_today","new_leads","whatsapp_chats","revenue_today","services_count","customers"]',
 '{"new":"Inquiry","qualified":"Interested","booked":"Booked","showed":"Served","sold":"Customer","lost":"Lost","fake":"Spam"}',
 '["Our services","Prices","Book or order","Timings","Location"]',
 'You serve the business described below. Talk only about the services and prices below. To book or order, ask for what is needed, when, and a name, then say the team will confirm. If asked for anything not listed, say the business does not offer it.',
 '["What your business does, in one line","Services with prices","Opening hours","How customers book or order","Location","What you do NOT offer"]')
ON CONFLICT (slug) DO NOTHING;

-- Existing businesses whose type is already clear from signup get it filled
-- in, so only unclear ones are asked at their next login. Never overwrites.
UPDATE tenants SET business_type = CASE
    WHEN industry ~* '(restaurant|cafe|café|food|bakery|catering|dhaba|kitchen)' THEN 'restaurant'
    WHEN industry ~* '(clinic|aesthetic|dental|dentist|medical|derma|skin|doctor|physio|hospital)' THEN 'clinic'
    WHEN industry ~* '(salon|barber|spa|beauty|parlou?r|nail|makeup)' THEN 'salon'
    WHEN industry ~* '(gym|fitness|yoga|crossfit|pilates)' THEN 'gym'
    WHEN industry ~* '(academy|school|training|tuition|institute|course|coaching)' THEN 'academy'
  END
 WHERE business_type IS NULL
   AND industry ~* '(restaurant|cafe|café|food|bakery|catering|dhaba|kitchen|clinic|aesthetic|dental|dentist|medical|derma|skin|doctor|physio|hospital|salon|barber|spa|beauty|parlou?r|nail|makeup|gym|fitness|yoga|crossfit|pilates|academy|school|training|tuition|institute|course|coaching)';

DO $verify$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['business_categories','business_knowledge','agent_knowledge_sync','agent_reply_flags','agent_reply_scan'] LOOP
    IF NOT (SELECT relforcerowsecurity FROM pg_class WHERE relname = t) THEN
      RAISE EXCEPTION 'verify failed: % row level security is not forced', t;
    END IF;
  END LOOP;
  IF (SELECT count(*) FROM business_categories) < 6 THEN
    RAISE EXCEPTION 'verify failed: business categories not seeded';
  END IF;
  RAISE NOTICE 'verify ok: business categories, knowledge and reply checks tenant isolated';
END
$verify$;

COMMIT;
