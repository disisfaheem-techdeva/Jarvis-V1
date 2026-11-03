"""Jarvis's own model order and personality rule (4 Nov 2026). No database or AI needed."""
from app import ai, console


def test_parse_chain():
    assert ai.parse_chain("gemini=gemini-3-flash-preview, gemini=gemini-3.6-flash,bogus=x") == [
        ("gemini", "gemini-3-flash-preview"), ("gemini", "gemini-3.6-flash")]
    assert ai.parse_chain("") == []


def test_chain_first_goes_first_only_inside_the_block(monkeypatch):
    monkeypatch.setenv("AI_MODEL_CHAIN", "gemini=gemini-3.5-flash-lite,gemini=gemini-3.6-flash")
    monkeypatch.setattr(ai, "_key_for", lambda p: "k" if p == "gemini" else "")
    base = ai.chain_entries()
    assert base[0] == ("gemini", "gemini-3.5-flash-lite")
    with ai.chain_first([("gemini", "gemini-3-flash-preview"), ("gemini", "gemini-3.6-flash"), ("openai", "x")]):
        got = ai.chain_entries()
    # Jarvis's models first, no duplicates, providers without a key dropped
    assert got == [("gemini", "gemini-3-flash-preview"), ("gemini", "gemini-3.6-flash"), ("gemini", "gemini-3.5-flash-lite")]
    assert ai.chain_entries() == base          # background work keeps its order


def test_fast_thinking_minimal_only_where_allowed():
    assert ai._gemini_fast_thinking("gemini-3-flash-preview") == {"thinkingLevel": "minimal"}
    assert ai._gemini_fast_thinking("gemini-3.8-flash") == {"thinkingLevel": "low"}


def test_jarvis_defaults_and_rules():
    assert console.JARVIS_CHAIN[0] == ("gemini", "gemini-3.6-flash")
    assert "Roman Urdu" in console.LANG_RULE and "greeting" in console.LANG_RULE
    assert "How can I help you manage" in console.PERSONA_RULE          # named as what NOT to say
    assert "never invent" in console.FACTS_RULE                         # personality never loosens the facts rule
