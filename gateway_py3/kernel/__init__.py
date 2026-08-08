"""GeoPilotKernel: the sole deep module exposed to callers.

Target architecture §3, §6.1. UI/HTTP/external agents/experiments adapt to
``submit``/``inspect``/``decide``/``resume`` only; they never reach the store,
planner, provider or ArcMap client directly.
"""
