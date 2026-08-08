"""HTTP adapter layer: the only way HTTP callers reach GeoPilotKernel (§9).

Endpoints under /api/v1 map to kernel operations only; Bridge callbacks
(/runs/:id/receipt etc.) route into the ArcMapRuntime bridge client with
lease fencing. No store, planner, provider or ArcMap client is reachable
from this layer directly.
"""
