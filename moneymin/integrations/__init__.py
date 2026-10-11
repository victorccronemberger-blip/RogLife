"""Contracts and registry for media source integrations."""

from .contracts import ProviderContract, ProviderId, ProviderRegistry, SelectionId
from .registry import get_provider, provider_ids_for, providers_for, selection_ids, supports_selection

__all__ = [
    "ProviderContract", "ProviderId", "ProviderRegistry", "SelectionId",
    "get_provider", "provider_ids_for", "providers_for", "selection_ids", "supports_selection",
]
