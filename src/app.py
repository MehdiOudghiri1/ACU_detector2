# src/app.py
from __future__ import annotations

import sys
import argparse
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

import yaml
from PySide6 import QtGui, QtWidgets
from ui import UIApp
from export import Exporter
from pdfio import PdfIO
from registry import PluginRegistry
from state import Store

# Welcome picker dialog
from welcome import WelcomeDialog


# -------------------------
# Config helpers
# -------------------------

_DEFAULT_CONFIG: Dict[str, Any] = {
    "pdf": {"cache_pages": 12, "workers": 2},
    "export": {"filename_template": "{tag}_p{page}.json", "pretty": True},
    "aliases": {},
}

# High-DPI rounding policy (Qt6+). Avoid deprecated AA_* attributes.
if hasattr(QtGui.QGuiApplication, "setHighDpiScaleFactorRoundingPolicy"):
    try:
        QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            QtGui.QGuiApplication.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass


def load_config(path: Optional[str]) -> Dict[str, Any]:
    """Load YAML config; fall back to defaults if missing/corrupt."""
    if not path:
        return _DEFAULT_CONFIG.copy()
    p = Path(path).expanduser()
    if not p.exists():
        return _DEFAULT_CONFIG.copy()
    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        # shallow-merge defaults
        out = _DEFAULT_CONFIG.copy()
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                tmp = out[k].copy()
                tmp.update(v)
                out[k] = tmp
            else:
                out[k] = v
        return out
    except Exception:
        return _DEFAULT_CONFIG.copy()


def _apply_aliases_from_config(registry: PluginRegistry, aliases: Dict[str, str]) -> None:
    """Best-effort alias registration based on whatever API the registry exposes."""
    for src, dst in (aliases or {}).items():
        if hasattr(registry, "add_alias"):
            registry.add_alias(src, dst)                # type: ignore[attr-defined]
        elif hasattr(registry, "register_alias"):
            registry.register_alias(src, dst)           # type: ignore[attr-defined]
        elif hasattr(registry, "alias"):
            registry.alias(src, dst)                    # type: ignore[attr-defined]
        # else: silently ignore


# -------------------------
# Main
# -------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ACU PDF Annotator")
    parser.add_argument("target", nargs="?", help="Path to a PDF file or a folder of PDFs")
    parser.add_argument("--config", "-c", help="Path to config.yaml", default=None)
    parser.add_argument("--recursive", "-r", action="store_true",
                        help="When target is a folder, include PDFs in subfolders")
    args = parser.parse_args(argv)

    # Load config
    config = load_config(args.config)

    # Registry (loads built-ins + aliases)
    registry = PluginRegistry()
    _apply_aliases_from_config(registry, config.get("aliases", {}))

    # Store + PdfIO
    store = Store(registry=registry)
    pdfio = PdfIO(
        cache_pages=int(config["pdf"].get("cache_pages", 12)),
        workers=int(config["pdf"].get("workers", 2)),
    )

    # UI (Qt app first; we may open a Welcome dialog)
    qt = QtWidgets.QApplication(sys.argv)

    ui = UIApp()  # on_save is set below so it can close over ui
    ui.store = store
    ui.registry = registry
    ui.pdf = pdfio
    ui.canvas.pdf = pdfio

    # Exporter + config
    exporter = Exporter(registry)
    filename_tmpl = config["export"].get("filename_template", "{tag}_p{page}.json")
    pretty = bool(config["export"].get("pretty", True))

    # ---- Determine playlist + roots (CLI target OR Welcome dialog) ----
    def _gather_from_target(target: str, recursive: bool) -> List[str]:
        p = Path(target).expanduser().resolve()
        if p.is_file() and p.suffix.lower() == ".pdf":
            return [str(p)]
        if p.is_dir():
            if recursive:
                pdfs = sorted(p.rglob("*.pdf"), key=lambda x: x.as_posix().lower())
            else:
                pdfs = sorted(
                    [c for c in p.iterdir() if c.is_file() and c.suffix.lower() == ".pdf"],
                    key=lambda x: x.as_posix().lower()
                )
            return [str(x) for x in pdfs]
        return []

    pdf_list: List[str] = []
    playlist_root: Path

    if args.target:
        tgt = Path(args.target).expanduser().resolve()
        pdf_list = _gather_from_target(args.target, args.recursive)
        if not pdf_list:
            QtWidgets.QMessageBox.warning(None, "No PDFs", "No PDF found at the given target.")
            return 1
        playlist_root = tgt if tgt.is_dir() else tgt.parent
    else:
        dlg = WelcomeDialog(parent=None)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return 0  # user canceled
        folder, pdfs = dlg.get_selection()
        if not folder or not pdfs:
            QtWidgets.QMessageBox.warning(None, "No PDFs", "No PDF found in the selected folder.")
            return 1
        playlist_root = folder
        pdf_list = [str(p) for p in pdfs]

    # Export root: sibling folder next to playlist_root
    export_root = playlist_root.parent / f"{playlist_root.name}_json"

    # ---- Immediate write export (writes to sibling export_root) ----
    def _write_current() -> None:
        """Build + validate + write JSON for current UI state immediately under export_root, preserving subfolders."""
        # Build/validate
        data = exporter.build(ui.store.state)
        ok, errors = exporter.validate(data)
        if not ok and errors:
            ui.toast(f"Validation errors: {len(errors)}", ttl=3.0)

        # Proposed filename from exporter (we'll only keep its basename)
        out_name = Path(exporter.filename(ui.store.state, filename_tmpl)).name

        # Compute relative path of the current PDF within playlist_root
        cur_pdf = Path(ui.store.state.pdf.path or "")
        try:
            rel_parent = cur_pdf.parent.relative_to(playlist_root)
        except Exception:
            # If PDF not under playlist_root, flatten
            rel_parent = Path("")

        # Final destination under the sibling export_root
        final_dir = (export_root / rel_parent)
        final_dir.mkdir(parents=True, exist_ok=True)
        final_path = final_dir / out_name

        # Dump + write
        text = exporter.dumps(data, pretty=pretty)
        final_path.write_text(text, encoding="utf-8")

        ui.toast(f"Saved → {final_path.relative_to(export_root)}")
        print(f"[export] wrote: {final_path}")

    # Hook: save before switching PDFs
    ui._on_before_next_pdf = _write_current
    # Replace UIApp default save with exporter-aware immediate write
    ui._on_save = _write_current

    # ---- Load playlist into UI and show ----
    ui.load_pdf_list(pdf_list)
    ui.show()
    return qt.exec()


if __name__ == "__main__":
    sys.exit(main())
