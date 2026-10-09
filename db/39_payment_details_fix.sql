-- 39_payment_details_fix.sql
-- On 29 Sep the deploy commands were pasted into billing_setup.py's
-- "How clients pay you" prompt by mistake. If that text is what got saved,
-- put TechDeva's JS Bank details there instead. Any other text is left alone.
BEGIN;

UPDATE platform_settings SET value =
'Bank transfer to:
TECH DEVA DIGITAL MARKETING AGENCY
JS Bank, Cavalry branch, Lahore
Account number: 0002923574
IBAN: PK19JSBL9561000002923574

After paying, press "I have paid" and add the transaction ID so we can unlock your account quickly.',
  updated_at = now()
WHERE key = 'payment_instructions'
  AND (value LIKE '%deploy.sh%' OR value LIKE '%git fetch%' OR value LIKE '%live_check%' OR value LIKE '%cd /opt/%');

COMMIT;
