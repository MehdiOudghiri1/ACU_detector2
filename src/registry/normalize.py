from __future__ import annotations
from typing import Any, Dict, Optional, Tuple

def _lc(x: Any) -> str:
    return str(x).strip().lower()

def normalize_enum(value: Any, mapping: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
    if value is None:
        return False, None, "Value is required."
    key = _lc(value)
    if key in mapping:
        return True, mapping[key], None
    # allow canonical values directly (case-insensitive)
    canon_vals = set(mapping.values())
    if value in canon_vals:
        return True, value, None
    lower_to_canon = { _lc(v): v for v in canon_vals }
    if _lc(value) in lower_to_canon:
        return True, lower_to_canon[_lc(value)], None
    return False, None, f"Invalid value: {value}"

def normalize_bool(value: Any) -> Tuple[bool, Any, Optional[str]]:
    if value is None:
        return False, None, "Value is required."
    if value in ("Yes", "No"):
        return True, value, None
    s = _lc(value)
    if s in {"y","yes","true","1"}:
        return True, "Yes", None
    if s in {"n","no","false","0"}:
        return True, "No", None
    return False, None, f"Invalid boolean: {value}"

def normalize_int(value: Any, min_val: Optional[int] = None, max_val: Optional[int] = None) -> Tuple[bool, Any, Optional[str]]:
    if value is None or value == "":
        return False, None, "Value is required."
    try:
        iv = int(value)
    except Exception:
        return False, None, f"Expected integer, got: {value}"
    if min_val is not None and iv < min_val:
        return False, None, f"Minimum is {min_val}"
    if max_val is not None and iv > max_val:
        return False, None, f"Maximum is {max_val}"
    return True, iv, None

def normalize_number(value: Any, min_val: Optional[float] = None, max_val: Optional[float] = None) -> Tuple[bool, Any, Optional[str]]:
    if value is None or value == "":
        return False, None, "Value is required."
    try:
        fv = float(value)
    except Exception:
        return False, None, f"Expected number, got: {value}"
    if min_val is not None and fv < min_val:
        return False, None, f"Minimum is {min_val}"
    if max_val is not None and fv > max_val:
        return False, None, f"Maximum is {max_val}"
    return True, fv, None
