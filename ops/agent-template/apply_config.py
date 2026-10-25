#!/usr/bin/env python3
"""T1 + T2 + T3(display): enforce the TechDeva baseline on an agent's config.yaml.

Run against the config the gateway generated on its first start, so every
other default stays whatever this Hermes version ships. Only these keys are
set, and each is read back afterwards; any mismatch exits non-zero.

  T1  agent.disabled_toolsets: tools a WhatsApp sales agent must never have.
  T2  model: provider gemini, default gemini-3.8-flash, fallbacks
      gemini-2.5-flash-lite then gemini-3-flash-preview. No OpenRouter: a free
      reasoning model in that chain once sent its private thinking to a
      customer. The key comes from GEMINI_API_KEY in the environment.
  T3  display.tool_progress: off (no "using tool X" lines to customers).
      display.busy_input_mode: queue and busy_ack_enabled: false (no
      "Interrupting current task" or "/busy" tip sent to a customer).
      display.long_running_notifications: false and agent.gateway_notify_interval: 0
      (no "⏳ Working — 3 min — iteration 2/60 ..." heartbeat to a customer while the
      model is slow; one reached a customer on 26 Oct 2026).
"""
import sys
import yaml

PATH = sys.argv[1] if len(sys.argv) > 1 else "/opt/data/config.yaml"
DISABLED = ["terminal", "file", "code_execution", "computer_use", "browser", "cronjob",
            "delegation", "homeassistant", "kanban", "discord_admin"]
MODEL = {"default": "gemini-3.8-flash", "provider": "gemini"}
FALLBACKS = [{"provider": "gemini", "model": "gemini-2.5-flash-lite"},
             {"provider": "gemini", "model": "gemini-3-flash-preview"}]

with open(PATH, encoding="utf-8") as f:
    cfg = yaml.safe_load(f) or {}
if not isinstance(cfg, dict):
    sys.exit("apply_config: %s is not a mapping" % PATH)

cfg.setdefault("model", {})
if not isinstance(cfg["model"], dict):
    cfg["model"] = {}
cfg["model"].update(MODEL)
# The config a fresh gateway writes can carry model.base_url pointing at
# openrouter.ai (seen on the 28 Sep probe). With provider gemini that would
# still send Gemini calls through OpenRouter, so any endpoint override goes.
for k in ("base_url", "api_key", "api_mode"):
    cfg["model"].pop(k, None)
cfg["fallback_providers"] = FALLBACKS
cfg["providers"] = {}                       # nothing but gemini is configured
cfg.setdefault("agent", {})["disabled_toolsets"] = DISABLED
cfg.setdefault("display", {})["tool_progress"] = "off"
cfg["display"]["show_reasoning"] = False
cfg["display"]["interim_assistant_messages"] = False
cfg["display"]["busy_input_mode"] = "queue"
cfg["display"]["busy_ack_enabled"] = False
cfg["display"]["long_running_notifications"] = False
cfg["agent"]["gateway_notify_interval"] = 0
# No speech-to-text engine is installed in the agent. With stt.enabled true the
# gateway sends the CUSTOMER "install faster-whisper ... /restart the gateway"
# setup text (seen 26 Oct 2026). Off: the agent itself asks them to type.
cfg.setdefault("stt", {})["enabled"] = False

with open(PATH + ".tmp", "w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
import os
os.replace(PATH + ".tmp", PATH)

# read back: prove what the gateway will load
back = yaml.safe_load(open(PATH, encoding="utf-8"))
problems = []
if back["model"].get("provider") != "gemini" or back["model"].get("default") != MODEL["default"]:
    problems.append("model")
if back.get("fallback_providers") != FALLBACKS:
    problems.append("fallback_providers")
if "openrouter" in str(back["model"]).lower() or any("openrouter" in str(x).lower() for x in back.get("fallback_providers", [])):
    problems.append("openrouter present")
if back["agent"].get("disabled_toolsets") != DISABLED:
    problems.append("agent.disabled_toolsets")
if str(back["display"].get("tool_progress")) not in ("off", "False"):
    problems.append("display.tool_progress")
if back["display"].get("busy_input_mode") != "queue" or back["display"].get("busy_ack_enabled") is not False:
    problems.append("display.busy")
if back["display"].get("long_running_notifications") is not False or back["agent"].get("gateway_notify_interval") != 0:
    problems.append("heartbeat")
if (back.get("stt") or {}).get("enabled") is not False:
    problems.append("stt")
if problems:
    sys.exit("apply_config: not applied: " + ", ".join(problems))
print("apply_config: model=%s/%s fallbacks=%s disabled_toolsets=%d tool_progress=%s" % (
    back["model"]["provider"], back["model"]["default"],
    ",".join(x["model"] for x in back["fallback_providers"]), len(back["agent"]["disabled_toolsets"]),
    back["display"]["tool_progress"]))
