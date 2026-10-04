# New-client WhatsApp agent template

Until 28 Sep 2026 every Hermes agent was set up by hand under `/docker/hermes-<name>/`,
and tonight's fixes lived only inside `hermes-agent-irfan`. This folder makes every
**new** agent start with them. It never touches an existing agent.

| | What | Where |
|---|---|---|
| T1 | Tool lockdown `agent.disabled_toolsets` | `apply_config.py` |
| T2 | Gemini only: `gemini-3.8-flash` → `gemini-2.5-flash-lite` → `gemini-3-flash-preview`; OpenRouter/OpenAI keys blanked in the agent | `apply_config.py`, compose `environment` |
| T3 | WhatsApp status messages ("Retrying…", "switching to fallback") suppressed in the gateway, **at image build**; `display.tool_progress: off` | `patch_gateway.py` (assert-guarded, idempotent), `apply_config.py` |
| T4 | `WHATSAPP_HOME_CHANNEL=<owner>@s.whatsapp.net` + `_NAME` per agent | compose `environment` |
| T5 | SOUL voice rules (female Zara, "aap", 2–4 lines Roman Urdu, one follow-up question) | `soul/voice_rules.md`, `apply_soul.py` |
| T6 | pairctl shares the agent's network (dashboard **Send** works, the bridge is loopback-only), writes a marker when a scan completes; `pairwatch.sh` restarts that agent once | `docker-compose.agent.yml`, `ops/pairctl/pairctl.mjs`, `pairwatch.sh` |
| T7 | Throwaway proof, then removal | `prove.sh` |
| T9 | Bridge packages carry the gateway's install stamp (`node_modules/.hermes-pkg-hash`), and once a number is paired `pairwatch.sh` switches WhatsApp on (`WHATSAPP_ENABLED=true` in that agent's `agent.env`) and recreates it. Without these two, a paired agent never starts WhatsApp (found 4 Oct 2026) | `new-agent.sh`, `pairwatch.sh` |

## Use

```
# once: the watcher (only acts on containers labelled techdeva.pairwatch=true)
( crontab -l; echo '* * * * * /opt/techdeva-platform/techdeva-platform/ops/agent-template/pairwatch.sh >> /var/log/techdeva-pairwatch.log 2>&1' ) | crontab -

# per new client
bash ops/agent-template/new-agent.sh --key noor --tenant <tenant uuid> \
     --owner-phone 923001234567 --home-name "Dr Noor" --business "Noor Aesthetics"

# prove the template (creates and removes a throwaway agent)
bash ops/agent-template/prove.sh
```

Secrets: `GEMINI_API_KEY` and `HERMES_INTERNAL_KEY` are read from the platform `.env`
at provisioning time into `/docker/hermes-<key>/agent.env` and `pairctl.env` (mode 600).
Nothing secret is in this repository.

## Existing agents

`hermes-agent-irfan` / `pairctl-irfan` and `hermes-workspace-ofsd-*` are refused by
name by every script here. Irfan's agent has T1–T5 by hand. What it does not have:
T3 survives only until its container is re-created (the stock image has no WhatsApp
filter), and its pairctl is on a different network, so dashboard Send cannot reach
its bridge. Moving him onto the template is a planned re-create, for Faheem to schedule.
