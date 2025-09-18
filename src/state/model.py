from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Any


class Mode(Enum):
    IDLE = auto()
    SECTION_ACTIVE = auto()
    FIELD_EDITING = auto()


class CompStatus(Enum):
    COMMITTED = auto()


@dataclass
class ComponentState:
    id: str
    type_id: str
    label: str
    fields: Dict[str, Any] = field(default_factory=dict)
    status: CompStatus = CompStatus.COMMITTED


@dataclass
class SectionState:
    id: str
    number: int
    name: Optional[str] = None
    length: Optional[int] = None  # inches; may be None
    components: List[ComponentState] = field(default_factory=list)


@dataclass
class EditingDraft:
    type_id: str
    label: str
    field_sequence: List[str]
    index: int = 0  # active field index in field_sequence
    values: Dict[str, Any] = field(default_factory=dict)  # partial fine


@dataclass
class PDFState:
    path: Optional[str] = None
    page: int = 0
    page_count: int = 0
    zoom: float = 1.0


@dataclass
class AppState:
    pdf: PDFState = field(default_factory=PDFState)
    sections: List[SectionState] = field(default_factory=list)
    active_section_id: Optional[str] = None
    mode: Mode = Mode.IDLE
    editing: Optional[EditingDraft] = None  # only in FIELD_EDITING
    dirty: bool = False
    last_autosave_at: float = 0.0

    def get_active_section(self) -> Optional[SectionState]:
        """Convenience lookup; O(n) but sections are small."""
        if self.active_section_id is None:
            return None
        for s in self.sections:
            if s.id == self.active_section_id:
                return s
        return None
