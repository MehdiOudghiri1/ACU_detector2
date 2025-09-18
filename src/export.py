# src/export.py
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from state import AppState
from registry import PluginRegistry


# ---------------------------
# Public Facade
# ---------------------------

class Exporter:
    """
    Build a clean export JSON from AppState, validate with registry specs,
    and produce a filename from a template.

    Typical usage:
        xp = Exporter(registry)
        data = xp.build(state)               # dict
        ok, errors = xp.validate(data)       # strict on required fields
        if ok:
            text = xp.dumps(data, pretty=True)
            out_path = xp.filename(state, "{tag}_p{page}.json")
            Path(out_path).write_text(text, encoding="utf-8")
    """

    def __init__(self, registry: PluginRegistry):
        self.registry = registry

    # ---- Build JSON (dict) ----
    def build(self, state: AppState) -> Dict[str, Any]:
        return _build_export_dict(state, self.registry)

    # ---- Validate against registry specs ----
    def validate(self, data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        return _validate_export_dict(data, self.registry)

    # ---- JSON text ----
    def dumps(self, data: Dict[str, Any], pretty: bool = True) -> str:
        return json.dumps(data, indent=2, ensure_ascii=False) if pretty else json.dumps(data, separators=(",", ":"))

    # ---- Filename from template ----
    def filename(self, state: AppState, template: str = "{tag}_p{page}.json") -> str:
        tag = _unit_tag_from_state(state) or "Unit"
        page = (state.pdf.page + 1) if state.pdf else 1
        tokens = {
            "tag": _sanitize_filename(tag),
            "page": page,
        }
        name = template.format(**tokens)
        # if no directory, place next to the PDF when available
        if os.path.dirname(name):
            return name
        pdf_dir = Path(state.pdf.path).parent if getattr(state.pdf, "path", None) else Path.cwd()
        return str(pdf_dir / name)

    # ---- (Optional) JSON Schema (informational) ----
    def schema(self) -> Dict[str, Any]:
        return _build_schema_from_registry(self.registry)


# ---------------------------
# Export construction
# ---------------------------

def _build_export_dict(state: AppState, registry: PluginRegistry) -> Dict[str, Any]:
    """
    Projects AppState → the "sample-shaped" JSON used in your docs.
    - Exact key casing (e.g., "Unit Tag", "Section Number", "ECM", etc.).
    - Each component is exported as:
        {
          "Label": "<spec label>",
          "<type_key>": { "<Field Label>": <value or null>, ... }
        }
    """
    unit_tag = _unit_tag_from_state(state)
    unit_props = _unit_properties_from_state(state)

    out: Dict[str, Any] = {
        "Unit Tag": unit_tag,
        "Unit Properties": unit_props,
    }
    # Sections
    sections_out: List[Dict[str, Any]] = []
    for sec in state.sections:
        sec_entry: Dict[str, Any] = {
            "Section Number": sec.number,
            "Length": sec.length,
        }
        # Include "Name" only if present
        if getattr(sec, "name", None):
            sec_entry["Name"] = sec.name

        comps_out: List[Dict[str, Any]] = []
        for comp in sec.components:
            spec = registry.get_spec(comp.type_id)
            type_key = spec.get("type_key", comp.type_id)
            comp_label = spec.get("label", comp.label or comp.type_id)

            field_block: Dict[str, Any] = {}
            for fname in comp.fields.keys():
                fdef = spec.get("fields", {}).get(fname, {})
                field_label = fdef.get("label") or _humanize_field(fname)
                value = comp.fields.get(fname, None)
                field_block[field_label] = value  # value is already normalized by reducer/registry

            comp_obj = {
                "Label": comp_label,
                type_key: field_block,
            }
            comps_out.append(comp_obj)

        if comps_out:
            sec_entry["Components"] = comps_out

        sections_out.append(sec_entry)

    out["Unit Properties"]["Unit size"]["Section quantity"] = len(sections_out)
    out["Unit Properties"]["Unit size"]["Section length"] = sections_out
    return out


def _unit_tag_from_state(state: AppState) -> Optional[str]:
    # Prefer explicit meta if present; fallback: filename stem
    meta_tag = getattr(getattr(state, "meta", None), "unit_tag", None)
    if meta_tag:
        return meta_tag
    pdf_path = getattr(state.pdf, "path", None)
    if pdf_path:
        try:
            return Path(pdf_path).stem
        except Exception:
            return None
    return None


def _unit_properties_from_state(state: AppState) -> Dict[str, Any]:
    # Gather top-level physical properties if you have them in state.meta.
    meta = getattr(state, "meta", None)
    fields = {
        "Indoor/Outdoor": getattr(meta, "indoor_outdoor", None),
        "Unit size": {
            "Unit Length": getattr(meta, "unit_length", None),
            "Width (with base)": getattr(meta, "width_with_base", None),
            "Height (base only)": getattr(meta, "base_height", None),
            "Cabinet height": getattr(meta, "cabinet_height", None),
            "Cabinet width": getattr(meta, "cabinet_width", None),
            # Filled later:
            "Section quantity": 0,
            "Section length": [],
        },
    }
    return fields


def _humanize_field(fname: str) -> str:
    """
    Convert 'face_and_bypass_damper' → 'Face and bypass damper'
    Prefer exact labels in specs, but fall back to this mapping.
    """
    special = {
        "heater_size": "Heater Size",
        "mounting_location": "Mounting location",
        "backdraft_dampers": "Backdraft dampers",
        "vertically_mounted": "Vertically mounted",
        "face_and_bypass_damper": "Face and bypass damper",
        "construction_type": "Construction type",
    }
    if fname in special:
        return special[fname]
    # default: underscores → spaces; Title Case except short words
    words = fname.replace("_", " ").split()
    if not words:
        return fname
    # lowercase all, then capitalize first word only (matches examples better)
    s = " ".join(words).lower()
    return s[0].upper() + s[1:]


def _sanitize_filename(s: str) -> str:
    return "".join(c for c in s if c not in r'\/:*?"<>|').strip() or "Unit"


# ---------------------------
# Validation (strict for required)
# ---------------------------

def _validate_export_dict(data: Dict[str, Any], registry: PluginRegistry) -> Tuple[bool, List[str]]:
    """
    Validate that all required fields for each component are present and non-null.
    Allow null for optional fields.
    Return (ok, errors).
    """
    errors: List[str] = []

    # Basic structure checks (lightweight)
    if "Unit Properties" not in data:
        errors.append("Missing: Unit Properties")
        return False, errors

    unit_size = data["Unit Properties"].get("Unit size", {})
    if not isinstance(unit_size, dict):
        errors.append("Invalid: Unit size should be an object")
        return False, errors

    sections = unit_size.get("Section length", [])
    if not isinstance(sections, list):
        errors.append("Invalid: Section length should be a list")
        return False, errors

    # Validate each component by spec
    for si, sec in enumerate(sections, start=1):
        comps = sec.get("Components", [])
        if not comps:
            continue
        for ci, comp_obj in enumerate(comps, start=1):
            label = comp_obj.get("Label")
            # Detect type_key by finding key that matches a registry spec
            type_key, fields = _detect_type_block(comp_obj, registry)
            if not type_key:
                errors.append(f"Section {si} Component {ci}: Could not determine component type for label '{label}'")
                continue
            # Map back to type_id
            type_id = registry.type_id_from_type_key(type_key) or type_key
            spec = registry.get_spec(type_id)

            # Required fields
            req = spec.get("required_fields", [])
            for fname in req:
                fdef = spec.get("fields", {}).get(fname, {})
                field_label = fdef.get("label") or _humanize_field(fname)
                if field_label not in fields or fields[field_label] is None:
                    errors.append(
                        f"Section {si} Component {ci} ({label}): missing required '{field_label}'"
                    )
            # Enum/Bool sanity
            for fname, fdef in spec.get("fields", {}).items():
                field_label = fdef.get("label") or _humanize_field(fname)
                val = fields.get(field_label, None)
                if val is None:
                    continue  # optional or not set
                ftype = fdef.get("type", "enum")
                if ftype == "enum":
                    canon_vals = set(fdef.get("map", {}).values())
                    if canon_vals and val not in canon_vals:
                        errors.append(
                            f"Section {si} Component {ci} ({label}): invalid value for '{field_label}': {val}"
                        )
                elif ftype == "bool":
                    if val not in {"Yes", "No"}:
                        errors.append(
                            f"Section {si} Component {ci} ({label}): boolean must be 'Yes' or 'No' for '{field_label}'"
                        )
                elif ftype == "int":
                    minv = fdef.get("min", None)
                    maxv = fdef.get("max", None)
                    if not isinstance(val, int):
                        errors.append(
                            f"Section {si} Component {ci} ({label}): integer expected for '{field_label}', got {val!r}"
                        )
                    else:
                        if minv is not None and val < minv:
                            errors.append(
                                f"Section {si} Component {ci} ({label}): '{field_label}' < {minv}"
                            )
                        if maxv is not None and val > maxv:
                            errors.append(
                                f"Section {si} Component {ci} ({label}): '{field_label}' > {maxv}"
                            )
                # numbers/strings are accepted as-is for MVP

    return (len(errors) == 0), errors


def _detect_type_block(comp_obj: Dict[str, Any], registry: PluginRegistry) -> Tuple[Optional[str], Dict[str, Any]]:
    """
    Given a component object like:
        {"Label":"Gas Heater", "GasHeater": {...}}
    Return ("GasHeater", {...})

    If multiple type-like keys exist, prefer the one that matches registry.type_key values.
    """
    reserved = {"Label"}
    candidates = [k for k in comp_obj.keys() if k not in reserved and isinstance(comp_obj[k], dict)]
    if not candidates:
        return None, {}
    # Prefer known type_keys
    known_type_keys = set(registry.type_keys())
    for k in candidates:
        if k in known_type_keys:
            return k, comp_obj[k]
    # otherwise fallback to the first
    k = candidates[0]
    return k, comp_obj[k]


# ---------------------------
# Schema (informational)
# ---------------------------

def _build_schema_from_registry(registry: PluginRegistry) -> Dict[str, Any]:
    """
    Produces a JSON-Schema-like dict (draft-07 flavored), mainly for tooling/IDE hints.
    Not used for validation (we do explicit checks above), but handy to export.
    """
    comps: Dict[str, Any] = {}
    for type_id, spec in registry.all_specs().items():
        type_key = spec.get("type_key", type_id)
        props: Dict[str, Any] = {}
        required_labels: List[str] = []
        for fname, fdef in spec.get("fields", {}).items():
            label = fdef.get("label") or _humanize_field(fname)
            ftype = fdef.get("type", "enum")
            node: Dict[str, Any] = {}
            if ftype == "enum":
                canon = sorted(set(fdef.get("map", {}).values()))
                node = {"type": "string", "enum": canon}
            elif ftype == "bool":
                node = {"type": "string", "enum": ["Yes", "No"]}
            elif ftype == "int":
                node = {"type": "integer"}
                if "min" in fdef: node["minimum"] = fdef["min"]
                if "max" in fdef: node["maximum"] = fdef["max"]
            else:
                node = {"type": "string"}
            # Optionals are allowed to be null
            node = {"anyOf": [node, {"type": "null"}]}
            props[label] = node

        for fname in spec.get("required_fields", []):
            fdef = spec.get("fields", {}).get(fname, {})
            required_labels.append(fdef.get("label") or _humanize_field(fname))

        comps[type_key] = {
            "type": "object",
            "properties": props,
            "required": required_labels,
            "additionalProperties": False,
        }

    top = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "ACU Export",
        "type": "object",
        "properties": {
            "Unit Tag": {"type": ["string", "null"]},
            "Unit Properties": {
                "type": "object",
                "properties": {
                    "Indoor/Outdoor": {"type": ["string", "null"]},
                    "Unit size": {
                        "type": "object",
                        "properties": {
                            "Unit Length": {"type": ["number", "null"]},
                            "Width (with base)": {"type": ["number", "null"]},
                            "Height (base only)": {"type": ["number", "null"]},
                            "Cabinet height": {"type": ["number", "null"]},
                            "Cabinet width": {"type": ["number", "null"]},
                            "Section quantity": {"type": "integer"},
                            "Section length": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "Section Number": {"type": "integer"},
                                        "Length": {"type": ["number", "null"]},
                                        "Name": {"type": ["string", "null"]},
                                        "Components": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "Label": {"type": "string"},
                                                    # Component type block injected dynamically.
                                                    # JSON Schema cannot easily do "oneOf" for all types here without a combinatorial explosion,
                                                    # so we declare it open and rely on our explicit validator above.
                                                },
                                                "additionalProperties": True,
                                            },
                                        },
                                    },
                                    "required": ["Section Number"],
                                    "additionalProperties": True,
                                },
                            },
                        },
                        "required": ["Section quantity", "Section length"],
                        "additionalProperties": True,
                    },
                },
                "required": ["Unit size"],
                "additionalProperties": True,
            },
        },
        "required": ["Unit Properties"],
        "additionalProperties": True,
        # Store component subschemas for tooling/reference:
        "definitions": {
            "components": comps
        }
    }
    return top
