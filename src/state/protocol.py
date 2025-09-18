from __future__ import annotations
from typing import Protocol, Optional, Any, Tuple, Dict


class RegistryProtocol(Protocol):
    """
    Minimal contract used by the reducer to stay decoupled from registry/plugins.

    Implementations must provide:
      - resolve_token(token) -> type_id | None
      - get_spec(type_id) -> dict with keys:
            label: str
            type_key: str (optional, used by export layer)
            field_sequence: list[str]
            required_fields: list[str]
      - validate_value(type_id, field, value) -> (ok: bool, normalized: Any, error: str|None)
    """
    def resolve_token(self, token: str) -> Optional[str]: ...
    def get_spec(self, type_id: str) -> Dict[str, Any]: ...
    def validate_value(self, type_id: str, field: str, value: Any) -> Tuple[bool, Any, Optional[str]]: ...
