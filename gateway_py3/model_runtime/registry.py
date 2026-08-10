"""Explicit provider registration and role binding resolution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .adapter import ProviderAdapter
from .contracts import ModelBinding, ProviderConnection


@dataclass(frozen=True)
class ProviderRoute:
    connection: ProviderConnection
    binding: ModelBinding
    adapter: ProviderAdapter


class ProviderRegistry:
    """Registry with exact connection lookup and no default or fallback path."""

    def __init__(self) -> None:
        self._connections: Dict[str, ProviderConnection] = {}
        self._adapters: Dict[str, ProviderAdapter] = {}

    def register(self, connection: ProviderConnection,
                 adapter: ProviderAdapter) -> None:
        if not isinstance(adapter, ProviderAdapter):
            raise TypeError("provider adapter does not implement ProviderAdapter.")
        if connection.connection_id in self._connections:
            raise ValueError("provider connection already registered: %s" % connection.connection_id)
        if adapter.connection_id != connection.connection_id:
            raise ValueError("provider adapter connection_id does not match its connection.")
        if adapter.provider_type != connection.provider_type:
            raise ValueError("provider adapter type does not match its connection.")
        self._connections[connection.connection_id] = connection
        self._adapters[connection.connection_id] = adapter

    def resolve(self, binding: ModelBinding) -> ProviderRoute:
        connection = self._connections.get(binding.connection_id)
        adapter = self._adapters.get(binding.connection_id)
        if connection is None or adapter is None:
            raise LookupError("provider connection is not registered: %s" % binding.connection_id)
        if binding.model_id not in connection.enabled_models:
            raise LookupError(
                "model %s is not enabled for connection %s"
                % (binding.model_id, binding.connection_id)
            )
        return ProviderRoute(connection=connection, binding=binding, adapter=adapter)

    def connection(self, connection_id: str) -> ProviderConnection:
        connection = self._connections.get(connection_id)
        if connection is None:
            raise LookupError("provider connection is not registered: %s" % connection_id)
        return connection
