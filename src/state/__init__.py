"""
Public API for the state package.

Import from here everywhere else, so you can refactor internals freely:
    from state import (
        AppState, SectionState, ComponentState, PDFState, EditingDraft,
        Mode, CompStatus,
        NewSection, StartComponent, SetFieldValue, NextField, PrevField,
        CommitComponent, CancelDraft, RenameSection, SetSectionLength,
        NavPage, SetPage, SetZoom, MarkSaved,
        RegistryProtocol, reduce, Store
    )
"""
from .model import (
    AppState, SectionState, ComponentState, PDFState, EditingDraft,
    Mode, CompStatus,
)
from .commands import (
    Command,
    NewSection, StartComponent, SetFieldValue, NextField, PrevField,
    CommitComponent, CancelDraft, RenameSection, SetSectionLength,
    NavPage, SetPage, SetZoom, MarkSaved, NextSection, PrevSection
)
from .protocol import RegistryProtocol
from .reducer import reduce
from .store import Store

__all__ = [
    # model
    "AppState", "SectionState", "ComponentState", "PDFState", "EditingDraft",
    "Mode", "CompStatus",
    # commands
    "Command",
    "NewSection", "StartComponent", "SetFieldValue", "NextField", "PrevField",
    "CommitComponent", "CancelDraft", "RenameSection", "SetSectionLength",
    "NavPage", "SetPage", "SetZoom", "MarkSaved", "NexSection", "PrevSection",
    # protocol & reducer & store
    "RegistryProtocol", "reduce", "Store",
]
