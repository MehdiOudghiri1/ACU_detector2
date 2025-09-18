from __future__ import annotations
from dataclasses import dataclass, field
from typing import List
import copy

from .model import AppState
from .commands import Command
from .reducer import reduce
from .protocol import RegistryProtocol


@dataclass
class Store:
    """
    Small wrapper around the pure reducer with undo/redo snapshots.

    Usage:
        store = Store(registry=my_registry)
        store.apply(NewSection(...))
        store.undo(); store.redo()
    """
    state: AppState = field(default_factory=AppState)
    registry: RegistryProtocol = field(default=None)  # inject at construction
    _undo: List[AppState] = field(default_factory=list)
    _redo: List[AppState] = field(default_factory=list)

    def apply(self, cmd: Command) -> AppState:
        self._undo.append(copy.deepcopy(self.state))
        self._redo.clear()
        self.state = reduce(self.state, cmd, self.registry)
        return self.state

    def undo(self) -> AppState:
        if not self._undo:
            return self.state
        self._redo.append(copy.deepcopy(self.state))
        self.state = self._undo.pop()
        return self.state

    def redo(self) -> AppState:
        if not self._redo:
            return self.state
        self._undo.append(copy.deepcopy(self.state))
        self.state = self._redo.pop()
        return self.state

    def clear_history(self) -> None:
        self._undo.clear()
        self._redo.clear()
