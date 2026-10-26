-- 81: API Manager, any provider (Rafay, 26 Oct 2026).
--
-- 80 stored Gemini keys for agents and other providers "for later". A Groq key
-- was added and could not be given to any agent. Now any OpenAI-compatible
-- provider, including a custom one (its own base URL), can run an agent:
--   base_url     the provider's OpenAI-compatible endpoint (NULL for gemini,
--                which agents reach natively; the preset URL for the others)
--   model        the model the agent asks for with this key
--   test_detail  what the last Test / Try a reply saw: HTTP code, time,
--                how many models, whether the chosen model is listed, reply
--                snippet. Never the key.
-- The provider list grows (openai, deepseek, together, xai, custom).
--
-- ROLLBACK (by hand, techdeva database only):
--   ALTER TABLE ai_keys DROP CONSTRAINT IF EXISTS ai_keys_provider_check;
--   DELETE FROM ai_keys WHERE provider NOT IN ('gemini','groq','openrouter','mistral','cerebras');
--   ALTER TABLE ai_keys ADD CONSTRAINT ai_keys_provider_check CHECK (provider IN ('gemini','groq','openrouter','mistral','cerebras'));
--   ALTER TABLE ai_keys DROP COLUMN IF EXISTS base_url, DROP COLUMN IF EXISTS model, DROP COLUMN IF EXISTS test_detail;
--   DELETE FROM _td_migrations WHERE name = '81_ai_keys_any_provider.sql';
BEGIN;

ALTER TABLE ai_keys ADD COLUMN IF NOT EXISTS base_url text;
ALTER TABLE ai_keys ADD COLUMN IF NOT EXISTS model text;
ALTER TABLE ai_keys ADD COLUMN IF NOT EXISTS test_detail jsonb;

ALTER TABLE ai_keys DROP CONSTRAINT IF EXISTS ai_keys_provider_check;
ALTER TABLE ai_keys ADD CONSTRAINT ai_keys_provider_check
  CHECK (provider IN ('gemini','groq','openrouter','mistral','cerebras','openai','deepseek','together','xai','custom'));
ALTER TABLE ai_keys DROP CONSTRAINT IF EXISTS ai_keys_base_url_check;
ALTER TABLE ai_keys ADD CONSTRAINT ai_keys_base_url_check
  CHECK (base_url IS NULL OR (base_url ~ '^https://[^\s]+$' AND length(base_url) <= 300));
ALTER TABLE ai_keys DROP CONSTRAINT IF EXISTS ai_keys_model_check;
ALTER TABLE ai_keys ADD CONSTRAINT ai_keys_model_check
  CHECK (model IS NULL OR (model ~ '^[A-Za-z0-9._:/@+-]{1,120}$'));

-- keys saved under 80 get their provider's endpoint and a default model
UPDATE ai_keys SET base_url = 'https://api.groq.com/openai/v1', model = coalesce(model, 'llama-3.3-70b-versatile')
 WHERE provider = 'groq' AND base_url IS NULL;
UPDATE ai_keys SET base_url = 'https://openrouter.ai/api/v1' WHERE provider = 'openrouter' AND base_url IS NULL;
UPDATE ai_keys SET base_url = 'https://api.mistral.ai/v1', model = coalesce(model, 'mistral-small-latest')
 WHERE provider = 'mistral' AND base_url IS NULL;
UPDATE ai_keys SET base_url = 'https://api.cerebras.ai/v1', model = coalesce(model, 'llama-3.3-70b')
 WHERE provider = 'cerebras' AND base_url IS NULL;

DO $verify$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'ai_keys' AND column_name = 'test_detail') THEN
    RAISE EXCEPTION 'verify: ai_keys.test_detail missing';
  END IF;
  IF EXISTS (SELECT 1 FROM ai_keys WHERE provider <> 'gemini' AND base_url IS NULL AND provider <> 'custom') THEN
    RAISE EXCEPTION 'verify: a non-gemini key has no endpoint';
  END IF;
  RAISE NOTICE 'verify: 81 ok';
END
$verify$;

COMMIT;
