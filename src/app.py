# src/app.py
from __future__ import annotations


import argparse
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional

from PySide6 import QtWidgets
from PySide6.QtCore import QTimer   # <-- add this


# local imports
from registry import PluginRegistry
from state import Store, MarkSaved
from export import Exporter
from ui import UIApp
from pdfio import PdfIO


try:
    import yaml  # optional but recommended
except Exception:
    yaml = None


# ---------------------------
# Config loading
# ---------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "aliases": {
        # token → type_id (you can have many tokens mapping to one component)
        "gas": "GasHeater",
        "ec": "ECM",
    },
    "export": {
        "filename_template": "{tag}_p{page}.json",
        "pretty": True,
    },
    "autosave": {
        "enabled": True,
        "seconds": 30,
        "dir": ".acu_autosave",
    },
    "pdf": {
        "cache_pages": 12,
        "workers": 2,
    },
}

def load_config(path: Optional[str]) -> Dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    if path:
        p = Path(path)
        if not p.exists():
            print(f"[app] config not found: {p} (using defaults)")
            return cfg
        if yaml is None:
            print("[app] PyYAML not installed; cannot read config.yaml. Using defaults.")
            return cfg
        with p.open("r", encoding="utf-8") as f:
            user = yaml.safe_load(f) or {}
        # shallow merge is enough for this MVP
        for k, v in user.items():
            if isinstance(v, dict) and k in cfg and isinstance(cfg[k], dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


# ---------------------------
# App bootstrap
# ---------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ACU PDF Annotator")
    parser.add_argument("target", nargs="?", help="Path to a PDF file or a folder of PDFs")
    parser.add_argument("--config", "-c", help="Path to config.yaml", default=None)
    parser.add_argument("--recursive", "-r", action="store_true", help="When target is a folder, include PDFs in subfolders")
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

    # UI
    qt = QtWidgets.QApplication(sys.argv)
    ui = UIApp()                         # on_save is set below so it can close over ui
    ui.store = store
    ui.registry = registry
    ui.pdf = pdfio
    ui.canvas.pdf = pdfio

    # Exporter + config
    exporter = Exporter(registry)
    filename_tmpl = config["export"].get("filename_template", "{tag}_p{page}.json")
    pretty = bool(config["export"].get("pretty", True))

    # ---- Batch context (app-side) ----
    staged: list[tuple[str, str]] = []   # (basename, json_text)
    playlist_root: Optional[Path] = None
    batch_mode = False

    def _suggest_basename_from_state() -> str:
        """Use Exporter.filename (based on ui.store.state.pdf.path/meta) but keep only the basename."""
        full = exporter.filename(ui.store.state, filename_tmpl)
        return Path(full).name

    def _stage_current() -> None:
        """Build+validate+dumps current JSON and push into 'staged' (NO disk IO)."""
        data = exporter.build(ui.store.state)
        ok, errs = exporter.validate(data)
        if not ok:
            # Keep strict; user should finish required fields before moving on.
            raise ValueError("\n".join(errs[:5]))
        text = exporter.dumps(data, pretty=pretty)
        staged.append((_suggest_basename_from_state(), text))

    def _write_all(include_current: bool) -> None:
        """Write all staged JSON (and optionally the current) into <input_folder_name>_jsons."""
        items = list(staged)
        if include_current:
            data = exporter.build(ui.store.state)
            ok, errs = exporter.validate(data)
            if not ok:
                raise ValueError("\n".join(errs[:5]))
            text = exporter.dumps(data, pretty=pretty)
            items.append((_suggest_basename_from_state(), text))

        if not items:
            return

        # Determine output dir: sibling next to the input folder (or the single PDF's folder)
        if playlist_root:
            out_dir = playlist_root.parent / f"{playlist_root.name}_jsons"
        else:
            pdf_path = getattr(ui.store.state.pdf, "path", None)
            if not pdf_path:
                raise ValueError("No PDF path to determine output folder")
            pdf_dir = Path(pdf_path).expanduser().resolve().parent
            out_dir = pdf_dir.parent / f"{pdf_dir.name}_jsons"

        out_dir.mkdir(parents=True, exist_ok=True)

        for basename, text in items:
            (out_dir / basename).write_text(text, encoding="utf-8")

    # Ctrl+S: write EVERY JSON now.
    # - Folder mode: write all STAGED + the CURRENT PDF into <input_folder>_jsons
    # - Single file: write the CURRENT PDF into <pdf_folder>_jsons (consistent output location)
    def on_save():
        if batch_mode:
            # Write everything we've staged (from previous PDFs) + the current PDF
            _write_all(include_current=True)
            staged.clear()  # avoid duplicates on subsequent Ctrl+S
            ui.store.apply(MarkSaved(when=time.time()))
            ui.toast("All staged JSONs (and current) written.", ttl=1.5)
        else:
            # Single-file: write current into <pdf_folder>_jsons for consistency
            data = exporter.build(ui.store.state)
            ok, errs = exporter.validate(data)
            if not ok:
                raise ValueError("\n".join(errs[:5]))
            text = exporter.dumps(data, pretty=pretty)
            # Determine output folder next to this single PDF's folder: <pdf_folder>_jsons
            pdf_path = getattr(ui.store.state.pdf, "path", None)
            if not pdf_path:
                raise ValueError("No PDF path to determine output folder")
            pdf_dir = Path(pdf_path).expanduser().resolve().parent
            out_dir = pdf_dir.parent / f"{pdf_dir.name}_jsons"
            out_dir.mkdir(parents=True, exist_ok=True)
            basename = Path(exporter.filename(ui.store.state, filename_tmpl)).name
            (out_dir / basename).write_text(text, encoding="utf-8")
            ui.store.apply(MarkSaved(when=time.time()))
            ui.toast("Saved JSON.", ttl=1.0)


    # Inject on_save now that it closes over ui
    ui._on_save = on_save if hasattr(ui, "_on_save") else None  # in case you wired via ctor elsewhere
    # Preferred: pass via constructor if your UIApp accepts on_save; otherwise keep callback in a menu/shortcut
    # If your constructor already accepts on_save, re-create as: ui = UIApp(on_save=on_save)

    # Give UI a chance to stage before moving to next PDF (plain 'n')
    ui._on_before_next_pdf = _stage_current

    # Open file or build playlist if 'target' provided
    if args.target:
        p = Path(args.target).expanduser().resolve()
        if p.is_dir():
            batch_mode = True
            playlist_root = p
            pattern = "**/*.pdf" if args.recursive else "*.pdf"
            pdfs = sorted(str(x) for x in p.glob(pattern))
            if pdfs:
                ui.load_pdf_list(pdfs)
                ui.toast(f"Loaded {len(pdfs)} PDFs from {p}", ttl=1.2)
            else:
                ui.toast(f"No PDFs found in {p}", ttl=2.5)
        elif p.is_file() and p.suffix.lower() == ".pdf":
            ui.load_pdf_list([str(p)])
            ui.toast(f"Opened {p.name}", ttl=1.2)
        else:
            ui.toast(f"Not a PDF or directory: {p}", ttl=3.0)

    # Minimal menubar (Open/Save/Exit)
    _install_menu(ui, on_save_cb=on_save)

    # Autosave: disable in batch mode (write everything at the end)
    if config.get("autosave", {}).get("enabled", True) and not batch_mode:
        _install_autosave_timer(ui, exporter, config)

    ui.show()
    return qt.exec()



# ---------------------------
# Helpers
# ---------------------------

def _apply_aliases_from_config(registry: PluginRegistry, aliases: Dict[str, str]) -> None:
    """
    Update the registry token resolver with extra aliases from config.
    Example:
      {"gas":"GasHeater","ec":"ECM"}
    """
    # We don’t assume private internals; use public API if you exposed one.
    # Minimal, pragmatic approach: extend the specs' aliases list.
    for token, type_id in (aliases or {}).items():
        try:
            spec = registry.get_spec(type_id)
        except Exception:
            continue
        spec.setdefault("aliases", [])
        if token not in spec["aliases"]:
            spec["aliases"].append(token)

def _install_menu(ui: UIApp, on_save_cb):
    bar = ui.menuBar()
    m_file = bar.addMenu("&File")

    act_open = m_file.addAction("Open…")
    act_open.setShortcut("Ctrl+O")
    act_open.triggered.connect(ui._open_pdf_dialog)

    act_save = m_file.addAction("Save JSON")
    act_save.setShortcut("Ctrl+S")
    act_save.triggered.connect(on_save_cb)

    m_file.addSeparator()
    act_exit = m_file.addAction("Exit")
    act_exit.setShortcut("Ctrl+Q")
    act_exit.triggered.connect(ui.close)

def _install_autosave_timer(ui: UIApp, exporter: Exporter, config: Dict[str, Any]):
    secs = int(config["autosave"].get("seconds", 30))
    out_dir = Path(config["autosave"].get("dir", ".acu_autosave"))
    out_dir.mkdir(parents=True, exist_ok=True)

    timer = QTimer(ui)
    timer.setInterval(max(5, secs) * 1000)

    def _tick():
        s = ui.store.state
        if not s.dirty:
            return
        # Export silently to autosave dir
        try:
            data = exporter.build(s)
            ok, errs = exporter.validate(data)
            if not ok:
                return  # don’t autosave invalid data
            text = exporter.dumps(data, pretty=True)
            tag = (s.meta.unit_tag or Path(getattr(s.pdf, "path", "")).stem or "Unit")
            page = (s.pdf.page + 1) if s.pdf else 1
            fname = f"{tag}_p{page}.autosave.json"
            (out_dir / fname).write_text(text, encoding="utf-8")
            # Do NOT mark saved in state for autosave; keep “dirty” to remind the user
        except Exception:
            pass

    timer.timeout.connect(_tick)
    timer.start()


if __name__ == "__main__":
    raise SystemExit(main())
