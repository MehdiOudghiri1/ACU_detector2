from __future__ import annotations
from typing import Dict, Any, Optional
from pathlib import Path

try:
    import yaml  # type: ignore
    _HAS_YAML = True
except Exception:
    _HAS_YAML = False

def load_plugin_specs(path: Optional[str | Path]) -> Dict[str, Dict[str, Any]]:
    """
    Load YAML plugin specs from a directory (optional).
    Returns a dict {type_id: spec}. Safe no-op if path missing or PyYAML absent.
    """
    specs: Dict[str, Dict[str, Any]] = {}
    if not path or not _HAS_YAML:
        return specs
    p = Path(path)
    if not p.exists() or not p.is_dir():
        return specs
    for yml in sorted(p.glob("*.yaml")):
        data = (yaml.safe_load(yml.read_text(encoding="utf-8")) or {})
        # Allow single or multi-component files
        if "components" in data and isinstance(data["components"], list):
            for spec in data["components"]:
                tid = spec.get("type_id")
                if not tid:
                    raise ValueError(f"{yml}: component missing 'type_id'")
                specs[tid] = spec
        else:
            tid = data.get("type_id")
            if not tid:
                raise ValueError(f"{yml}: spec missing 'type_id'")
            specs[tid] = data
    return specs
