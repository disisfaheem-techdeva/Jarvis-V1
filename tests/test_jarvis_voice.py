"""Jarvis's voice: Roman Urdu is spoken as Urdu, English as English (6 Nov 2026)."""
from app import console


def test_roman_urdu_is_heard_as_urdu():
    assert console.is_roman_urdu("Walaikum Assalam! Main bilkul theek hoon, aap sunayein?")
    assert console.is_roman_urdu("Aaj aap ke 3 naye leads aaye hain aur ek follow-up pending hai.")
    assert console.is_roman_urdu("Chahat ke liye kal shaam 6 baje ka call follow-up add kar diya hai.")


def test_english_stays_english():
    assert not console.is_roman_urdu("You have 3 new leads and 1 follow-up waiting today.")
    assert not console.is_roman_urdu("Done.")
    assert not console.is_roman_urdu("")


def test_short_style_and_voice_setting():
    # a long instruction is read out loud by the speech model: keep it short
    assert len(console.URDU_STYLE) < 60 and "Pakistani" in console.URDU_STYLE
    assert console.JARVIS_VOICE
