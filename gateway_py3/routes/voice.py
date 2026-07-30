from __future__ import annotations

from gateway_py3 import voice as voice_service


def transcribe(state, payload):
    return voice_service.transcribe_voice(payload)


def correct(state, payload):
    if "context" in payload:
        raise ValueError("voice context is captured only inside a run.")
    return voice_service.correct_transcribed_voice(payload)
