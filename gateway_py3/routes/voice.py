from __future__ import annotations

from gateway_py3 import voice as voice_service


def transcribe(state, payload):
    return voice_service.transcribe_voice(payload)


def correct(state, payload):
    context = payload.get("context") if isinstance(payload.get("context"), dict) else None
    if context is None:
        stored_context = state.store.get_state("arcmap_context")
        context = stored_context.get("value") if isinstance(stored_context, dict) else None
    return voice_service.correct_transcribed_voice(payload, stored_context=context)
