from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional
import time


class Command:
    """Marker base class for all commands (intents)."""
    pass


@dataclass
class NewSection(Command):
    name: Optional[str] = None
    length: Optional[int] = None  # inches


@dataclass
class StartComponent(Command):
    """Start a component by token OR explicit type_id (token preferred from keyboard)."""
    token: Optional[str] = None
    type_id: Optional[str] = None


@dataclass
class SetFieldValue(Command):
    value: Any
    auto_advance: bool = False   # ⟵ AJOUT


@dataclass
class NextField(Command):
    """Advance to next field; if at last field and required set, commit."""


@dataclass
class PrevField(Command):
    """Go back one field (does not unset values)."""


@dataclass
class CommitComponent(Command):
    """Force commit even if not at last field (used when UI auto-commits after final input)."""


@dataclass
class CancelDraft(Command):
    """Cancel the current component draft (confirm in UI before dispatching)."""


@dataclass
class RenameSection(Command):
    section_id: str
    name: str


@dataclass
class SetSectionLength(Command):
    section_id: str
    length: Optional[int]  # inches or None


@dataclass
class NavPage(Command):
    delta: int  # +1 next, -1 prev


@dataclass
class SetPage(Command):
    page: int  # 0-based, clamped


@dataclass
class SetZoom(Command):
    zoom: float  # absolute; reducer clamps bounds


@dataclass
class MarkSaved(Command):
    """Signals that an external save succeeded; clears dirty + updates autosave timestamp."""
    when: float = time.time()

@dataclass
class PrevSection(Command): 
    """Go to previous section (if any)."""
@dataclass
class NextSection(Command): 
    """Go to next section (if any)."""

