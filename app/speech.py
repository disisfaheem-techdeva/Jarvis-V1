"""Speech gateway (Phase 2, 9 Nov 2026): one place Jarvis hears and speaks through.

Which provider is used is a setting, never a code change (decision 6 in the Jarvis
plan is Faheem's: which speech-to-text provider, and whether to pay for one).

    SPEECH_STT_PROVIDER   browser (default) | gemini | openai
        browser  the server does not hear: each browser's own speech recognition is
                 used, exactly as before this file existed
        gemini   audio is sent to Gemini (the platform's Gemini key)
        openai   audio is sent to an OpenAI-compatible /v1/audio/transcriptions
                 (OPENAI_BASE_URL, OPENAI_API_KEY): OpenAI Whisper, or a self-hosted one
    SPEECH_STT_MODEL      the model for the chosen provider
    SPEECH_TTS_PROVIDER   gemini (default) | openai | off
        gemini   Jarvis's current voice (app/console._tts), unchanged
        openai   an OpenAI-compatible /v1/audio/speech
    SPEECH_TTS_MODEL, SPEECH_TTS_VOICE

Server-side hearing asks for Roman Urdu (Latin letters) for Urdu speech, so a spoken
"cheeni" or "aata" comes back in the form shops write as a product's other name
(db/93) and is matched by the till. Every call is metered (app/usage.py).
"""
from __future__ import annotations

import base64
import io
import os
import re
import time
import wave

import requests

STT_PROVIDERS = ("browser", "gemini", "openai")
TTS_PROVIDERS = ("gemini", "openai", "off")
MAX_AUDIO = 3_000_000          # about two minutes of compressed speech; a till turn is seconds
AUDIO_TYPES = ("audio/webm", "audio/ogg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/mpeg", "audio/aac")
STT_TIMEOUT = 30
HEAR = ("Write down exactly what is said in this recording. It is a shopkeeper talking to an assistant and may be "
        "Urdu, Punjabi-flavoured Urdu, Roman Urdu, English, or a mix. Write Urdu words in Roman Urdu (Latin letters), "
        "e.g. 'do kilo cheeni aur ek sabun ka bill bana do'. Write numbers as digits. Answer with the words only; "
        "if nothing is said, answer with nothing.")


class SpeechError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def stt_provider():
    p = (os.environ.get("SPEECH_STT_PROVIDER") or "browser").strip().lower()
    return p if p in STT_PROVIDERS else "browser"


def tts_provider():
    p = (os.environ.get("SPEECH_TTS_PROVIDER") or "gemini").strip().lower()
    return p if p in TTS_PROVIDERS else "gemini"


def capabilities():
    """For a client: does the server hear (it then sends audio), and does it speak. Names no provider."""
    return {"input": "server" if stt_provider() != "browser" else "client",
            "output": None if tts_provider() == "off" else "audio/wav"}


def decode_audio(data_url):
    """(bytes, mime) from a data URL such as data:audio/webm;codecs=opus;base64,..."""
    m = re.match(r"^data:([a-z]+/[a-z0-9.+-]+)(?:;[^,;]*)*;base64,(.*)$", str(data_url or ""), re.S | re.I)
    if not m:
        raise SpeechError(400, "Send the recording as audio.")
    mime = m.group(1).lower()
    if mime not in AUDIO_TYPES:
        raise SpeechError(400, "That kind of recording is not supported. Use the till or the dashboard to record.")
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except ValueError:
        raise SpeechError(400, "The recording could not be read.")
    if not raw:
        raise SpeechError(400, "The recording is empty.")
    if len(raw) > MAX_AUDIO:
        raise SpeechError(413, "That recording is too long. Say it in a sentence or two.")
    return raw, ("audio/wav" if mime == "audio/x-wav" else mime)


def _gemini_stt(raw, mime, lang):
    from .ai import base_url
    from .platform_key import gemini_key
    key = gemini_key()
    if not key:
        raise SpeechError(503, "Hearing is set to Gemini, but this server has no Gemini key.")
    model = (os.environ.get("SPEECH_STT_MODEL") or "gemini-2.5-flash").strip()
    r = requests.post("%s/v1beta/models/%s:generateContent" % (base_url("gemini"), model), params={"key": key},
                      timeout=STT_TIMEOUT,
                      json={"contents": [{"parts": [{"inline_data": {"mime_type": mime.split(";")[0],
                                                                     "data": base64.b64encode(raw).decode()}},
                                                    {"text": HEAR}]}],
                            "generationConfig": {"maxOutputTokens": 600, "temperature": 0}})
    if r.status_code >= 400:
        raise SpeechError(429 if r.status_code == 429 else 503, "Hearing is busy right now (%s). Type it instead." % r.status_code)
    c = (r.json().get("candidates") or [{}])[0]
    return "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts", [])), "gemini:" + model


def _openai_stt(raw, mime, lang):
    from .ai import base_url
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise SpeechError(503, "Hearing is set to an OpenAI-compatible service, but no key is set.")
    model = (os.environ.get("SPEECH_STT_MODEL") or "whisper-1").strip()
    ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav", "audio/mp4": "m4a", "audio/mpeg": "mp3",
           "audio/aac": "aac"}.get(mime, "webm")
    data = {"model": model, "prompt": "Roman Urdu or English shop talk, e.g. do kilo cheeni, ek sabun, aata."}
    if lang in ("ur", "en"):
        data["language"] = lang
    r = requests.post(base_url("openai") + "/v1/audio/transcriptions", timeout=STT_TIMEOUT,
                      headers={"Authorization": "Bearer " + key}, data=data,
                      files={"file": ("speech." + ext, raw, mime)})
    if r.status_code >= 400:
        raise SpeechError(429 if r.status_code == 429 else 503, "Hearing is busy right now (%s). Type it instead." % r.status_code)
    return str(r.json().get("text") or ""), "openai:" + model


def transcribe(data_url, lang=None):
    """{text, model} for a recording, with the server's chosen provider. Raises SpeechError."""
    p = stt_provider()
    if p == "browser":
        raise SpeechError(501, "This server does not hear recordings: your browser's own speech recognition is used.")
    from . import usage
    why = usage.check()
    if why:
        raise SpeechError(402, why)
    raw, mime = decode_audio(data_url)
    started = time.time()
    try:
        text, model = (_gemini_stt if p == "gemini" else _openai_stt)(raw, mime, lang)
    except requests.RequestException:
        raise SpeechError(503, "Hearing could not be reached. Type it instead.")
    text = re.sub(r"\s+", " ", str(text or "")).strip()[:2000]
    # kind must be one ai_usage accepts (db/71 CHECK); the feature says what it was
    usage.charge("voice", True, provider=p, model=model, ms=(time.time() - started) * 1000, chars_out=len(text),
                 feature="speech_in")
    return {"text": text, "model": model}


# ------------------------------------------------------------------ speaking
def _pcm_or_wav(data):
    if data[:4] == b"RIFF":
        return data
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(data)
    return buf.getvalue()


def tts(text, urdu):
    """WAV bytes for Jarvis's spoken reply. Raises fastapi.HTTPException as console._tts does."""
    from fastapi import HTTPException
    p = tts_provider()
    if p == "off":
        raise HTTPException(503, "Voice is off on this server.")
    if p == "gemini":
        from .console import _tts
        return _tts(text, urdu)
    from . import usage
    from .ai import base_url
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise HTTPException(503, "Voice is set to an OpenAI-compatible service, but no key is set.")
    why = usage.check()
    if why:
        raise HTTPException(402, why)
    model = (os.environ.get("SPEECH_TTS_MODEL") or "gpt-4o-mini-tts").strip()
    started = time.time()
    try:
        r = requests.post(base_url("openai") + "/v1/audio/speech", timeout=15, headers={"Authorization": "Bearer " + key},
                          json={"model": model, "voice": (os.environ.get("SPEECH_TTS_VOICE") or "alloy").strip(),
                                "input": text, "response_format": "wav",
                                "instructions": "Warm, natural Pakistani Urdu accent." if urdu else "Warm, friendly, clear."})
    except requests.RequestException:
        raise HTTPException(503, "Voice is busy right now.")
    if r.status_code >= 400:
        raise HTTPException(503, "Voice is busy right now (%s)." % r.status_code)
    usage.charge("voice", True, provider="openai", model=model, ms=(time.time() - started) * 1000, chars_in=len(text))
    return _pcm_or_wav(r.content)
