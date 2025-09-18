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
    parser.add_argument("pdf", nargs="?", help="Path to PDF to open")
    parser.add_argument("--config", "-c", help="Path to config.yaml", default=None)
    args = parser.parse_args(argv)

    # Load config
    config = load_config(args.config)

    # Registry (loads built-in specs; then apply aliases from config)
    registry = PluginRegistry()
    _apply_aliases_from_config(registry, config.get("aliases", {}))

    # Store + PdfIO
    store = Store(registry=registry)
    pdfio = PdfIO(
        cache_pages=int(config["pdf"].get("cache_pages", 12)),
        workers=int(config["pdf"].get("workers", 2)),
    )

    # Exporter + on_save callback (uses config)
    exporter = Exporter(registry)
    def on_save():
        data = exporter.build(store.state)
        ok, errs = exporter.validate(data)
        if not ok:
            # Let UI toast the first few errors via exception; UIApp catches and shows
            raise ValueError("\n".join(errs[:5]))
        text = exporter.dumps(data, pretty=bool(config["export"].get("pretty", True)))
        out_path = exporter.filename(store.state, config["export"].get("filename_template", "{tag}_p{page}.json"))
        Path(out_path).write_text(text, encoding="utf-8")
        store.apply(MarkSaved(when=time.time()))

    # UI
    qt = QtWidgets.QApplication(sys.argv)
    ui = UIApp(on_save=on_save)
    ui.store = store  # inject same store we created
    ui.registry = registry
    ui.pdf = pdfio
    ui.canvas.pdf = pdfio  # ensure canvas points to the same PdfIO

    # Open PDF if provided
    if args.pdf:
        p = Path(args.pdf)
        if p.exists():
            try:
                pdfio.open(str(p))
                # reflect into state so UI footer / filename templating work
                ui.store.state.pdf.path = str(p)
                ui.store.state.pdf.page_count = pdfio.page_count
                ui.store.state.pdf.page = 0
                ui.canvas.update()
                ui.toast(f"Opened {p.name}", ttl=1.2)
            except Exception as e:
                ui.toast(f"Failed to open PDF: {e}", ttl=3.0)
        else:
            ui.toast(f"PDF not found: {p}", ttl=3.0)

    # Minimal menubar (Open/Save/Exit)
    _install_menu(ui, on_save_cb=on_save)

    # Autosave (silent): every N seconds if dirty
    if config.get("autosave", {}).get("enabled", True):
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
