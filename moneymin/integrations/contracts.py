"""Contratos e identificadores das integrações de mídia.

Os módulos provider continuam públicos em ``moneymin.ego4d``,
``moneymin.holoassist`` e ``moneymin.nymeria``. O registry descreve essas
facades sem impor uma assinatura artificial comum às APIs de catálogo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias

ProviderId: TypeAlias = Literal["ego4d", "holoassist", "nymeria"]
SelectionId: TypeAlias = Literal["ego4d", "holoassist", "nymeria", "ambos", "all"]


@dataclass(frozen=True, slots=True)
class ProviderContract:
    """Identidade estável e ponto de entrada compatível de um provider."""

    provider_id: ProviderId
    module: str
    catalog_operation: str
    prepare_operation: str
    prepare_module: str = "moneymin.campaign"


class ProviderRegistry(Protocol):
    """Interface mínima para resolver seleções para providers conhecidos."""

    def supports_selection(self, selection: str) -> bool: ...

    def providers_for(self, selection: str) -> tuple[ProviderContract, ...]: ...
