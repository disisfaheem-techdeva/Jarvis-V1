-- 37_payment_details.sql
-- TechDeva's bank details (given by Faheem, 29 Sep 2026) as the "How clients
-- pay you" text every client sees on Plan & Billing and the lock screen.
-- These are meant to be shown to clients; they are not a secret.
-- Fills the text only when none has been typed yet: never overwrites.
BEGIN;

INSERT INTO platform_settings (key, value) VALUES ('payment_instructions',
'Bank transfer to:
TECH DEVA DIGITAL MARKETING AGENCY
JS Bank, Cavalry branch, Lahore
Account number: 0002923574
IBAN: PK19JSBL9561000002923574

After paying, press "I have paid" and add the transaction ID so we can unlock your account quickly.')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
  WHERE platform_settings.value = '';

COMMIT;
