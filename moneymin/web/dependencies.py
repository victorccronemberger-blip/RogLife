"""Explicit, restricted dependency bindings used by HTTP feature modules.

The composition root owns global compatibility exports and application-local
state. Features receive only the dependency names they declare. No feature
imports the composition root, and no globals are copied into feature modules.
"""
from __future__ import annotations

from functools import partial, update_wrapper
from typing import Any, MutableMapping


class DependencyBindings:
    __slots__ = ("_global_namespace", "_local_namespace", "_required")

    def __init__(self, global_namespace: MutableMapping[str, Any],
                 local_namespace: MutableMapping[str, Any] | None = None,
                 required: tuple[str, ...] | None = None):
        object.__setattr__(self, "_global_namespace", global_namespace)
        object.__setattr__(self, "_local_namespace", local_namespace if local_namespace is not None else {})
        object.__setattr__(self, "_required", frozenset(required) if required is not None else None)

    def scope(self, required: tuple[str, ...]) -> DependencyBindings:
        return DependencyBindings(self._global_namespace, self._local_namespace, required)

    def provide(self, name: str, value: Any) -> None:
        self._local_namespace[name] = value

    def bind(self, implementation, required: tuple[str, ...]):
        handler = partial(implementation, self.scope(required))
        update_wrapper(handler, implementation)
        return handler

    def _check(self, name: str) -> None:
        if self._required is not None and name not in self._required:
            raise AttributeError(f"Undeclared feature dependency: {name}")

    def __getattr__(self, name: str) -> Any:
        self._check(name)
        if name in self._local_namespace:
            return self._local_namespace[name]
        try:
            return self._global_namespace[name]
        except KeyError:
            raise AttributeError(f"Missing feature dependency: {name}") from None

    def __setattr__(self, name: str, value: Any) -> None:
        self._check(name)
        if name in self._local_namespace:
            self._local_namespace[name] = value
        else:
            self._global_namespace[name] = value
