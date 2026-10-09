-- 40_payment_wording.sql
-- Faheem, 29 Sep 2026: clients pay into the JS Bank account by bank transfer,
-- or from JazzCash / Easypaisa to that account; the team verifies within
-- 2 hours. Updates the text from db/37/39 only if it is still exactly that
-- text (anything Faheem typed himself is left alone).
BEGIN;

UPDATE platform_settings SET value =
'Pay into our JS Bank account:
TECH DEVA DIGITAL MARKETING AGENCY
JS Bank, Cavalry branch, Lahore
Account number: 0002923574
IBAN: PK19JSBL9561000002923574

Bank transfer, or from JazzCash / Easypaisa: Send money > Bank account > JS Bank, then the account number or IBAN above.

After paying, press "I have paid" and add the transaction ID. Our team verifies payments within 2 hours.',
  updated_at = now()
WHERE key = 'payment_instructions'
  AND value = 'Bank transfer to:
TECH DEVA DIGITAL MARKETING AGENCY
JS Bank, Cavalry branch, Lahore
Account number: 0002923574
IBAN: PK19JSBL9561000002923574

After paying, press "I have paid" and add the transaction ID so we can unlock your account quickly.';

COMMIT;
