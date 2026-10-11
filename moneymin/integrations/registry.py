"""Catálogo central das fontes de mídia e seleções de campanha."""
from __future__ import annotations

from .contracts import ProviderContract, ProviderId, SelectionId

_PROVIDERS: dict[ProviderId, ProviderContract] = {
    "ego4d": ProviderContract(
        "ego4d", "moneymin.ego4d", "list_clips", "prepare_clip"),
    "holoassist": ProviderContract(
        "holoassist", "moneymin.holoassist", "list_clips", "prepare_holoassist_clip"),
    "nymeria": ProviderContract(
        "nymeria", "moneymin.nymeria", "automatic_candidates", "prepare_nymeria_clip"),
}

# Preserva a semântica de campanha já usada na UI: "ambos" combina Ego4D e
# Nymeria; "all" também inclui HoloAssist.
_SELECTIONS: dict[SelectionId, tuple[ProviderId, ...]] = {
    "holoassist": ("holoassist",),
    "ego4d": ("ego4d",),
    "nymeria": ("nymeria",),
    "ambos": ("ego4d", "nymeria"),
    "all": ("ego4d", "holoassist", "nymeria"),
}


def supports_selection(selection: str) -> bool:
    """Indica se a seleção é uma fonte única ou agregada conhecida."""
    return str(selection).strip().lower() in _SELECTIONS


def selection_ids() -> tuple[SelectionId, ...]:
    """Return the public provider selections in their compatibility order."""
    return tuple(_SELECTIONS)


def providers_for(selection: str) -> tuple[ProviderContract, ...]:
    """Resolve fonte/alias para seus contratos, sem importar providers."""
    key = str(selection).strip().lower()
    provider_ids = _SELECTIONS.get(key)
    if provider_ids is None:
        supported = ", ".join(_SELECTIONS)
        raise ValueError(f"provider deve ser {supported}")
    return tuple(_PROVIDERS[provider_id] for provider_id in provider_ids)


def provider_ids_for(selection: str) -> tuple[ProviderId, ...]:
    """Retorna IDs para dispatch de consumidores que preservam APIs próprias."""
    return tuple(contract.provider_id for contract in providers_for(selection))


def get_provider(provider_id: str) -> ProviderContract:
    """Busca um provider individual pelo ID estável."""
    key = str(provider_id).strip().lower()
    provider = _PROVIDERS.get(key)
    if provider is None:
        raise ValueError(f"provider desconhecido: {provider_id}")
    return provider


__all__ = ["get_provider", "provider_ids_for", "providers_for", "selection_ids", "supports_selection"]
