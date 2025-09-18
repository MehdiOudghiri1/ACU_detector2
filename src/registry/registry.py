from __future__ import annotations
from typing import Dict, Any, Optional, Tuple, Iterable
from pathlib import Path

from state.protocol import RegistryProtocol  # uses your existing protocol
from .normalize import normalize_enum, normalize_bool, normalize_int, normalize_number
from .specs import BUILTIN_SPECS
from .loader import load_plugin_specs

def _lc(x: Any) -> str:
    return str(x).strip().lower()

class PluginRegistry(RegistryProtocol):
    """
    Concrete registry with:
      - Built-in specs for all ACU components
      - Optional YAML plugin overrides/extensions
      - Optional extra_specs dict injection (for tests)
    """

    def __init__(self, plugins_dir: Optional[str | Path] = None, extra_specs: Optional[Dict[str, Dict[str, Any]]] = None):
        self._specs: Dict[str, Dict[str, Any]] = {}
        self._aliases: Dict[str, str] = {}

        # 1) built-ins
        for tid, spec in BUILTIN_SPECS.items():
            self._register_spec(tid, spec)

        # 2) caller-provided extra specs (override/extend)
        if extra_specs:
            for tid, spec in extra_specs.items():
                self._register_spec(tid, spec)

        # 3) YAML plugins (override/extend)
        for tid, spec in load_plugin_specs(plugins_dir).items():
            self._register_spec(tid, spec)

        self._rebuild_alias_index()

    # ----- Protocol methods -----

    def resolve_token(self, token: str) -> Optional[str]:
        if not token:
            return None
        return self._aliases.get(_lc(token))

    def get_spec(self, type_id: str) -> Dict[str, Any]:
        return self._specs.get(type_id, {})

    def validate_value(self, type_id: str, field: str, value: Any) -> Tuple[bool, Any, Optional[str]]:
        spec = self._specs.get(type_id)
        if not spec:
            return False, None, f"Unknown component type: {type_id}"
        fields = spec.get("fields", {})
        fdef = fields.get(field)
        if not fdef:
            return False, None, f"Unknown field for {type_id}: {field}"

        ftype = fdef.get("type", "enum")
        if ftype == "enum":
            mapping = { _lc(k): v for k, v in fdef.get("map", {}).items() }
            return normalize_enum(value, mapping)
        if ftype == "bool":
            return normalize_bool(value)
        if ftype == "int":
            return normalize_int(value, fdef.get("min"), fdef.get("max"))
        if ftype == "number":
            return normalize_number(value, fdef.get("min"), fdef.get("max"))

        return False, None, f"Unsupported field type '{ftype}' for {type_id}.{field}"

    # ----- internal plumbing -----

    def _register_spec(self, type_id: str, spec: Dict[str, Any]) -> None:
        spec = dict(spec)
        spec.setdefault("label", type_id)
        spec.setdefault("type_key", type_id)
        spec.setdefault("field_sequence", [])
        spec.setdefault("required_fields", [])
        spec.setdefault("fields", {})
        spec.setdefault("aliases", [])

        # ensure sequence fields exist
        fields = spec["fields"]
        for fname in spec["field_sequence"]:
            if fname not in fields:
                raise ValueError(f"Spec for {type_id} references unknown field '{fname}' in field_sequence")

        self._specs[type_id] = spec

    def _rebuild_alias_index(self) -> None:
        self._aliases.clear()
        for tid, spec in self._specs.items():
            tokens: Iterable[str] = list(spec.get("aliases", [])) + [tid, spec.get("label", "")]
            for t in tokens:
                if not t:
                    continue
                self._aliases.setdefault(_lc(t), tid)  # first writer wins
