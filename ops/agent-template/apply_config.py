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
cfg["fallback_providers"] = FALLBACKS
cfg["providers"] = {}                       # nothing but gemini is configured
cfg.setdefault("agent", {})["disabled_toolsets"] = DISABLED
cfg.setdefault("display", {})["tool_progress"] = "off"
cfg["display"]["show_reasoning"] = False
cfg["display"]["interim_assistant_messages"] = False

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
if any("openrouter" in str(x).lower() for x in back.get("fallback_providers", [])) or back["model"].get("provider") == "openrouter":
    problems.append("openrouter present")
if back["agent"].get("disabled_toolsets") != DISABLED:
    problems.append("agent.disabled_toolsets")
if str(back["display"].get("tool_progress")) not in ("off", "False"):
    problems.append("display.tool_progress")
if problems:
    sys.exit("apply_config: not applied: " + ", ".join(problems))
print("apply_config: model=%s/%s fallbacks=%s disabled_toolsets=%d tool_progress=%s" % (
    back["model"]["provider"], back["model"]["default"],
    ",".join(x["model"] for x in back["fallback_providers"]), len(back["agent"]["disabled_toolsets"]),
    back["display"]["tool_progress"]))
