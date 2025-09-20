from __future__ import annotations
from typing import List, Any, Optional
from dataclasses import replace
import copy
import time

from .model import (
    AppState, SectionState, ComponentState, EditingDraft,
    Mode, CompStatus,
)
from .commands import (
    Command, NewSection, StartComponent, SetFieldValue, NextField, PrevField,
    CommitComponent, CancelDraft, RenameSection, SetSectionLength,
    NavPage, SetPage, SetZoom, MarkSaved, ResetSection,
    PrevSection, NextSection,
)
from .protocol import RegistryProtocol


def reduce(state: AppState, cmd: Command, registry: RegistryProtocol) -> AppState:
    """
    Pure state transformer. Never mutates the input state.
    Raises ValueError on impossible transitions; UI should generally guard.
    """
    s = copy.deepcopy(state)  # safe and simple; optimize later if profiling warrants

    # --- Section creation ---
    if isinstance(cmd, NewSection):
        if s.mode == Mode.FIELD_EDITING and s.editing:
            raise ValueError("Finish or cancel the current component before creating a new section.")
        number = (s.sections[-1].number + 1) if s.sections else 1
        sec = SectionState(
            id=_new_id("sec", number),
            number=number,
            name=cmd.name,
            length=cmd.length,
        )
        s.sections.append(sec)
        s.active_section_id = sec.id
        s.mode = Mode.SECTION_ACTIVE
        s.dirty = True
        return s

    # --- Start component ---
    if isinstance(cmd, StartComponent):
        if s.mode != Mode.SECTION_ACTIVE:
            raise ValueError("StartComponent requires an active section.")
        active = s.get_active_section()
        if not active:
            raise ValueError("No active section found.")

        type_id = cmd.type_id or (registry.resolve_token(cmd.token) if cmd.token else None)
        if not type_id:
            raise ValueError(f"Unknown component token/type: {cmd.token or cmd.type_id}")

        spec = registry.get_spec(type_id)
        base_seq: List[str] = list(spec.get("field_sequence", []))  # canonical / full
        label: str = spec.get("label", type_id)

        draft = EditingDraft(
            type_id=type_id,
            label=label,
            field_sequence=[],                 # will be computed below
            base_field_sequence=base_seq,      # keep canonical list forever
            index=0,
            values={f: None for f in base_seq} # values for ALL potential fields
        )

        # initial visibility from the canonical sequence
        draft.field_sequence = _maybe_recompute_visible_sequence(
            draft.type_id, draft.base_field_sequence, draft.values
        )

        s.editing = draft
        s.mode = Mode.FIELD_EDITING
        return s

    # --- Set field value ---
    if isinstance(cmd, SetFieldValue):
        if s.mode != Mode.FIELD_EDITING or not s.editing:
            raise ValueError("SetFieldValue requires an active draft.")
        draft = s.editing
        if draft.index < 0 or draft.index >= len(draft.field_sequence):
            raise ValueError("Field index out of range.")

        # Name of the field we're editing *in the visible (filtered) sequence*
        field_name = draft.field_sequence[draft.index]

        # Validate/normalize
        ok, normalized, err = registry.validate_value(draft.type_id, field_name, cmd.value)
        if not ok:
            raise ValueError(err or f"Invalid value for {field_name}: {cmd.value}")

        # Commit the value
        draft.values[field_name] = normalized
        s.dirty = True

        # Apply conditional auto-defaults for Coil toggles BEFORE recomputing visibility
        if draft.type_id == "Coil" and field_name in ("kits_included", "controllers_included"):
            _coil_apply_auto_values(draft.values, field_name, normalized)

        # Recompute visible sequence ALWAYS from canonical base_field_sequence
        old_seq = draft.field_sequence
        new_seq = _maybe_recompute_visible_sequence(
            draft.type_id, draft.base_field_sequence, draft.values
        )
        draft.field_sequence = new_seq

        # Adjust index if the current field vanished or we moved beyond bounds
        if draft.index >= len(new_seq):
            draft.index = max(0, len(new_seq) - 1)
        else:
            cur_name = old_seq[draft.index] if draft.index < len(old_seq) else None
            if cur_name and cur_name not in new_seq:
                # try to jump to the next visible field after the old position
                nxt_idx = None
                for f in old_seq[draft.index + 1:]:
                    if f in new_seq:
                        nxt_idx = new_seq.index(f)
                        break
                draft.index = (nxt_idx if nxt_idx is not None else min(draft.index, len(new_seq) - 1))

        # Optional auto-advance (used by UI for one-tap numerics)
        if getattr(cmd, "auto_advance", False):
            last_idx = len(draft.field_sequence) - 1
            if draft.index >= last_idx and _all_required_set(draft, registry):
                return _commit_current_draft(s)
            draft.index = min(draft.index + 1, last_idx)

        return s

    # --- Reset section (clear components; optional length clear) ---
    if isinstance(cmd, ResetSection):
        sec = _find_section(s, cmd.section_id)
        if not sec:
            raise ValueError("Unknown section.")
        sec.components.clear()
        if cmd.clear_length:
            sec.length = None
        s.editing = None
        s.mode = Mode.SECTION_ACTIVE
        s.dirty = True
        return s

    # --- Next / Prev field ---
    if isinstance(cmd, NextField):
        if s.mode != Mode.FIELD_EDITING or not s.editing:
            return s
        draft = s.editing
        last_idx = len(draft.field_sequence) - 1
        if draft.index >= last_idx and _all_required_set(draft, registry):
            return _commit_current_draft(s)
        draft.index = min(draft.index + 1, last_idx)
        return s

    if isinstance(cmd, PrevField):
        if s.mode != Mode.FIELD_EDITING or not s.editing:
            return s
        draft = s.editing
        draft.index = max(draft.index - 1, 0)
        return s

    # --- Commit / Cancel draft ---
    if isinstance(cmd, CommitComponent):
        if s.mode != Mode.FIELD_EDITING or not s.editing:
            return s
        if not _all_required_set(s.editing, registry):
            raise ValueError("Cannot commit: required fields are missing.")
        return _commit_current_draft(s)

    if isinstance(cmd, CancelDraft):
        if s.mode != Mode.FIELD_EDITING or not s.editing:
            return s
        s.editing = None
        s.mode = Mode.SECTION_ACTIVE
        return s

    # --- Section edits ---
    if isinstance(cmd, RenameSection):
        sec = _find_section(s, cmd.section_id)
        if not sec:
            raise ValueError("Unknown section.")
        sec.name = cmd.name
        s.dirty = True
        return s

    if isinstance(cmd, SetSectionLength):
        sec = _find_section(s, cmd.section_id)
        if not sec:
            raise ValueError("Unknown section.")
        sec.length = cmd.length
        s.dirty = True
        return s

    # --- PDF navigation (not dirty) ---
    if isinstance(cmd, NavPage):
        if s.pdf.page_count > 0:
            s.pdf.page = max(0, min(s.pdf.page + cmd.delta, s.pdf.page_count - 1))
        return s

    if isinstance(cmd, SetPage):
        if s.pdf.page_count > 0:
            s.pdf.page = max(0, min(cmd.page, s.pdf.page_count - 1))
        return s

    if isinstance(cmd, SetZoom):
        s.pdf.zoom = max(0.25, min(cmd.zoom, 4.0))
        return s

    # --- Save acknowledgment ---
    if isinstance(cmd, MarkSaved):
        s.dirty = False
        s.last_autosave_at = cmd.when
        return s

    # --- Section navigation (not dirty) ---
    if isinstance(cmd, PrevSection):
        if s.sections:
            cur_idx = 0
            if s.active_section_id:
                for i, sec in enumerate(s.sections):
                    if sec.id == s.active_section_id:
                        cur_idx = i
                        break
            new_idx = max(0, cur_idx - 1)
            s.active_section_id = s.sections[new_idx].id
            s.mode = Mode.SECTION_ACTIVE
        return s

    if isinstance(cmd, NextSection):
        if s.sections:
            cur_idx = 0
            if s.active_section_id:
                for i, sec in enumerate(s.sections):
                    if sec.id == s.active_section_id:
                        cur_idx = i
                        break
            new_idx = min(len(s.sections) - 1, cur_idx + 1)
            s.active_section_id = s.sections[new_idx].id
            s.mode = Mode.SECTION_ACTIVE
        return s

    # Unhandled command → no-op (future-proof)
    return s


# ----- helpers -----

def _new_id(prefix: str, n: int) -> str:
    return f"{prefix}-{n}-{int(time.time()*1000)%1_000_000}"

def _find_section(s: AppState, section_id: str) -> Optional[SectionState]:
    for sec in s.sections:
        if sec.id == section_id:
            return sec
    return None

def _all_required_set(draft: EditingDraft, registry: RegistryProtocol) -> bool:
    spec = registry.get_spec(draft.type_id)
    req = set(spec.get("required_fields", []))
    for f in req:
        if draft.values.get(f, None) is None:
            return False
    return True

def _commit_current_draft(s: AppState) -> AppState:
    draft = s.editing
    if not draft:
        return s
    sec = s.get_active_section()
    if not sec:
        raise ValueError("No active section.")
    # Ensure all declared base fields exist (optional can be None)
    for f in draft.base_field_sequence:
        draft.values.setdefault(f, None)
    comp = ComponentState(
        id=_new_id("cmp", len(sec.components) + 1),
        type_id=draft.type_id,
        label=draft.label,
        fields=copy.deepcopy(draft.values),
        status=CompStatus.COMMITTED,
    )
    sec.components.append(comp)
    s.editing = None
    s.mode = Mode.SECTION_ACTIVE
    s.dirty = True
    return s

# --- Coil conditional fields: visibility + auto-defaults --------------------

def _coil_visible_fields(sequence: list[str], values: dict) -> list[str]:
    """
    Filter Coil field_sequence based on current values.
    - If kits_included != 'Yes' → hide kits_qty, kits_mount
    - If controllers_included != 'Yes' → hide controllers_qty, controllers_mount
    """
    kits_yes = values.get("kits_included") == "Yes"
    ctrl_yes = values.get("controllers_included") == "Yes"

    def _keep(fname: str) -> bool:
        if fname in ("kits_qty", "kits_mount") and not kits_yes:
            return False
        if fname in ("controllers_qty", "controllers_mount") and not ctrl_yes:
            return False
        return True

    return [f for f in sequence if _keep(f)]


def _maybe_recompute_visible_sequence(type_id: str, base_sequence: list[str], values: dict) -> list[str]:
    """Central hook: per-type dynamic sequence filtering (always from canonical base)."""
    if type_id == "Coil":
        return _coil_visible_fields(base_sequence, values)
    return base_sequence


def _coil_apply_auto_values(values: dict, changed_field: str, new_val: Any):
    """
    Enforce defaults when toggling Coil parents:
    - kits_included: 'No' → kits_qty=0, kits_mount='None'; 'Yes' → clear (None, None)
    - controllers_included: same logic
    """
    if changed_field == "kits_included":
        if new_val == "No":
            values["kits_qty"] = 0
            values["kits_mount"] = "None"
        elif new_val == "Yes":
            values["kits_qty"] = None
            values["kits_mount"] = None

    elif changed_field == "controllers_included":
        if new_val == "No":
            values["controllers_qty"] = 0
            values["controllers_mount"] = "None"
        elif new_val == "Yes":
            values["controllers_qty"] = None
            values["controllers_mount"] = None
