"""Leaf policy for explicit provider indexing versus campaign catalogue reads."""
from contextlib import contextmanager
from contextvars import ContextVar

_PREPARED_ONLY = ContextVar("prepared_catalog_only", default=False)
_PERSIST_INDEX = ContextVar("persist_provider_index", default=False)


class CatalogPreparationRequired(ValueError):
    """The campaign may read an index, but cannot initiate cold classification."""


def require_prepared_index(provider):
    if _PREPARED_ONLY.get():
        raise CatalogPreparationRequired(
            f"O índice {provider} precisa ser preparado ou atualizado na tela Catálogos.")


@contextmanager
def prepared_catalog_read():
    token = _PREPARED_ONLY.set(True)
    try:
        yield
    finally:
        _PREPARED_ONLY.reset(token)


@contextmanager
def persist_catalog_indexes():
    token = _PERSIST_INDEX.set(True)
    try:
        yield
    finally:
        _PERSIST_INDEX.reset(token)


def persist_index_requested():
    return _PERSIST_INDEX.get()


def prepared_only_requested():
    return _PREPARED_ONLY.get()
