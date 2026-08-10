"""Installed provider adapters and their strict construction seam."""

from ..contracts import ProviderConnection
from ..credentials import CredentialVault
from .anthropic_compatible import (
    ANTHROPIC_COMPATIBLE_PROVIDER_TYPES,
    AnthropicCompatibleAdapter,
)
from .minimax import MiniMaxAdapter
from .openai_compatible import (
    OPENAI_COMPATIBLE_PROVIDER_TYPES,
    OpenAICompatibleAdapter,
)


def create_provider_adapter(connection: ProviderConnection,
                            vault: CredentialVault):
    if connection.provider_type == "minimax":
        return MiniMaxAdapter(connection, vault)
    if connection.provider_type in OPENAI_COMPATIBLE_PROVIDER_TYPES:
        return OpenAICompatibleAdapter(connection, vault)
    if connection.provider_type in ANTHROPIC_COMPATIBLE_PROVIDER_TYPES:
        return AnthropicCompatibleAdapter(connection, vault)
    raise ValueError("unsupported provider_type: %s" % connection.provider_type)


__all__ = (
    "AnthropicCompatibleAdapter", "MiniMaxAdapter", "OpenAICompatibleAdapter",
    "create_provider_adapter",
)
