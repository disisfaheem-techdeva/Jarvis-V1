"""TechDeva td_speed: Gemini 3 Flash answers with "minimal" thinking (Rafay, 28 Oct 2026).

Hermes clamps reasoning_effort "minimal" to thinkingLevel "low" for Gemini 3
Flash. Measured on 28 Oct 2026 with the TechDeva key: low thinks ~300 tokens
and answers in ~2.3 s; minimal thinks none and answers in ~1.1 s. A WhatsApp
sales reply needs no thinking. With agent.reasoning_effort: minimal in
config.yaml, this plugin sends thinkingLevel "minimal" (and no thought parts)
to the models in MINIMAL_OK. Only models measured to accept it are listed:
gemini-3.8-flash answered "minimal" with HTTP 400 on 28 Oct 2026. Every other
model and effort is left to Hermes (minimal -> low).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MINIMAL_OK = {"gemini-3-flash-preview"}


def _patch():
    from agent.transports import chat_completions as cc
    original = cc._build_gemini_thinking_config
    if getattr(original, "_td_speed", False):
        return

    def build(model, reasoning_config):
        try:
            m = (model or "").strip().lower().split("/")[-1]
            effort = str((reasoning_config or {}).get("effort") or "").strip().lower()
            if (isinstance(reasoning_config, dict) and reasoning_config.get("enabled") is not False
                    and effort == "minimal" and m in MINIMAL_OK):
                return {"includeThoughts": False, "thinkingLevel": "minimal"}
        except Exception:  # noqa: BLE001 -- fall back to Hermes' own choice
            logger.exception("[TD] speed: could not read the reasoning config")
        return original(model, reasoning_config)

    build._td_speed = True
    cc._build_gemini_thinking_config = build
    logger.info("[TD] speed: Gemini 3 Flash minimal thinking on")


def register(ctx):
    try:
        _patch()
    except Exception:  # noqa: BLE001 -- the agent must still start
        logger.exception("[TD] speed: patch not applied")
