# src/ui.py
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Optional, List, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from registry import PluginRegistry
from state import (
    Store, Mode,
    NewSection, StartComponent, SetFieldValue, NextField, PrevField,
    CancelDraft, NavPage, SetZoom, MarkSaved, SetSectionLength, ResetSection
)
from pdfio import PdfIO
from dimension_extractor import analyze_page


# -------------------------
# Action model (semantic)
# -------------------------

@dataclass(frozen=True)
class Action:
    kind: str
    payload: Optional[object] = None

    # Kinds
    NEW_SECTION = "NEW_SECTION"
    TOKEN_APPEND = "TOKEN_APPEND"
    TOKEN_SUBMIT = "TOKEN_SUBMIT"
    TOKEN_CLEAR = "TOKEN_CLEAR"
    SET_FIELD_VALUE = "SET_FIELD_VALUE"
    NEXT_FIELD = "NEXT_FIELD"
    PREV_FIELD = "PREV_FIELD"
    CANCEL_DRAFT = "CANCEL_DRAFT"
    SAVE = "SAVE"
    NAV_PAGE = "NAV_PAGE"
    SET_ZOOM = "SET_ZOOM"
    OPEN_PDF = "OPEN_PDF"
    UNDO = "UNDO"
    REDO = "REDO"
    NOOP = "NOOP"
    TOKEN_BACKSPACE = "TOKEN_BACKSPACE"
    PREV_SECTION = "PREV_SECTION"
    NEXT_SECTION = "NEXT_SECTION"
    # Type-ahead (field)
    FIELDBUF_APPEND = "FIELDBUF_APPEND"
    FIELDBUF_BACKSPACE = "FIELDBUF_BACKSPACE"
    FIELDBUF_CLEAR = "FIELDBUF_CLEAR"
    NEXT_PDF = "NEXT_PDF"

    # Resets (standardized)
    RESET_SECTION = "RESET_SECTION"     # Ctrl+R → reset active section (incl. length) and prompt length again
    START_OVER = "START_OVER"           # Ctrl+Shift+R → clear all annotations for this PDF


# -------------------------
# KeyRouter (mode-aware)
# -------------------------

class KeyRouter:
    """Translate raw key events into Actions, with global + mode-specific priority."""

    def route(self, state, event: QtGui.QKeyEvent, token_active: bool) -> Action:
        key = event.key()
        mods = event.modifiers()
        text = event.text() or ""

        # --- Global shortcuts (always) ---
        if mods & QtCore.Qt.ControlModifier:
            # Reset section only: Ctrl+R
            if key == QtCore.Qt.Key_R and not (mods & QtCore.Qt.ShiftModifier):
                return Action(Action.RESET_SECTION)
            # Start over (entire PDF): Ctrl+Shift+R
            if key == QtCore.Qt.Key_R and (mods & QtCore.Qt.ShiftModifier):
                return Action(Action.START_OVER)

            if key in (QtCore.Qt.Key_O,):
                return Action(Action.OPEN_PDF)
            if key in (QtCore.Qt.Key_S,):
                return Action(Action.SAVE)
            if key in (QtCore.Qt.Key_P,):
                return Action(Action.NAV_PAGE, -1)
            if key in (QtCore.Qt.Key_N,):
                return Action(Action.NAV_PAGE, +1)
            if key in (QtCore.Qt.Key_Plus, QtCore.Qt.Key_Equal):
                return Action(Action.SET_ZOOM, min(getattr(state, "pdf", None).zoom * 1.1 if state.pdf else 1.0 * 1.1, 4.0))
            if key in (QtCore.Qt.Key_Minus,):
                return Action(Action.SET_ZOOM, max(getattr(state, "pdf", None).zoom / 1.1 if state.pdf else 1.0 / 1.1, 0.25))
            if key in (QtCore.Qt.Key_0,):
                return Action(Action.SET_ZOOM, 1.0)
            if key in (QtCore.Qt.Key_Z,):
                return Action(Action.UNDO)
            if key in (QtCore.Qt.Key_Y,):
                return Action(Action.REDO)
            if key == QtCore.Qt.Key_Up:
                return Action(Action.PREV_SECTION)
            if key == QtCore.Qt.Key_Down:
                return Action(Action.NEXT_SECTION)

        # --- Mode-specific: FIELD_EDITING ---
        if state.mode == Mode.FIELD_EDITING:
            if key == QtCore.Qt.Key_Tab and not (mods & QtCore.Qt.ShiftModifier):
                return Action(Action.NEXT_FIELD)
            if key == QtCore.Qt.Key_Backtab or (key == QtCore.Qt.Key_Tab and (mods & QtCore.Qt.ShiftModifier)):
                return Action(Action.PREV_FIELD)
            # Type-ahead editing behaviour
            if key == QtCore.Qt.Key_Backspace:
                return Action(Action.FIELDBUF_BACKSPACE)
            if key == QtCore.Qt.Key_Escape:
                return Action(Action.FIELDBUF_CLEAR)
            # Character input for current field (passes to type-ahead / numeric handler in dispatcher)
            if text and not (mods & QtCore.Qt.ControlModifier):
                ch = text.strip()
                if ch:
                    return Action(Action.FIELDBUF_APPEND, ch)
            return Action(Action.NOOP)

        # --- Next PDF (plain 'n') ---
        # Only when NOT editing a field and NOT typing a token
        if key == QtCore.Qt.Key_N and not (mods & QtCore.Qt.ControlModifier) and not token_active:
            return Action(Action.NEXT_PDF)

        # --- SECTION_ACTIVE (and IDLE behaves the same for MVP) ---
        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            # If in token typing, submit token. Otherwise create new section.
            if token_active:
                return Action(Action.TOKEN_SUBMIT)
            return Action(Action.NEW_SECTION)

        if key == QtCore.Qt.Key_Escape and token_active:
            return Action(Action.TOKEN_CLEAR)

        # just before token typing section (and after FIELD_EDITING block)
        if token_active and key == QtCore.Qt.Key_Backspace:
            return Action(Action.TOKEN_BACKSPACE)

        # Token typing: accept letters/digits/_-
        if text and text.isprintable() and not (mods & QtCore.Qt.ControlModifier):
            ch = text.strip()
            if ch:
                # Start or append token while in SECTION_ACTIVE
                return Action(Action.TOKEN_APPEND, ch)

        return Action(Action.NOOP)


# -------------------------
# Prompt/HUD model
# -------------------------

@dataclass
class FieldChip:
    name: str
    value: Optional[str]
    active: bool

@dataclass
class HudModel:
    title: str
    fields: List[FieldChip]
    hints: List[str]             # help lines
    token_ui: Optional[str]
    foot: str  # e.g., filename / page / zoom
    toasts: List[str]
    # Options visualisation for active field (prefix highlight)
    options_visual: List[Tuple[str, int]]  # (label, match_prefix_len) ; 0 if no match
    ambiguous: bool
    no_match: bool


class PromptBuilder:
    def __init__(self, registry: PluginRegistry):
        self.registry = registry

    def build(self, state, token_buffer: Optional[str], toasts: List[str], field_buffer: str = "") -> HudModel:
        # Title + fields + hints
        title = ""
        fields: List[FieldChip] = []
        hints: List[str] = []
        token_ui: Optional[str] = None
        options_visual: List[Tuple[str, int]] = []
        ambiguous = False
        no_match = False

        if state.mode == Mode.FIELD_EDITING and state.editing:
            spec = self.registry.get_spec(state.editing.type_id)
            title = spec.get("label", state.editing.type_id)
            seq = state.editing.field_sequence
            idx = state.editing.index
            for i, fname in enumerate(seq):
                val = state.editing.values.get(fname)
                fields.append(FieldChip(name=fname, value="?" if val is None else str(val), active=(i == idx)))

            # Hints for the active field
            if 0 <= idx < len(seq):
                fdef = spec.get("fields", {}).get(seq[idx], {})
                ftype = fdef.get("type", "enum")

                # Always display full labels for enum/bool
                def _labels_for():
                    if ftype == "bool":
                        return ["Yes", "No"]
                    if ftype == "enum":
                        # unique-preserving
                        return list(dict.fromkeys(fdef.get("map", {}).values()))
                    return []

                labels = _labels_for()
                if labels:
                    # Visual matching of buffer
                    def _fold(s: str) -> str:
                        import unicodedata as _ud
                        s = _ud.normalize("NFD", s)
                        s = "".join(c for c in s if _ud.category(c) != "Mn")
                        return s.casefold()
                    fb = _fold(field_buffer) if field_buffer else ""
                    matches = []
                    for L in labels:
                        if fb and _fold(L).startswith(fb):
                            matches.append(L)
                            options_visual.append((L, len(field_buffer)))
                        else:
                            options_visual.append((L, 0))
                    if field_buffer:
                        if matches:
                            ambiguous = len(matches) > 1
                            if ambiguous:
                                hints.append("keep typing…")
                        else:
                            no_match = True
                            hints.append("no match")
                        hints.append(f"typed: {field_buffer}▎")
                    # Options line
                    hints.insert(0, "Options: " + " / ".join(labels))

                elif ftype == "int":
                    minv = fdef.get("min"); maxv = fdef.get("max")
                    if minv is not None and maxv is not None:
                        hints = [f"[{minv}..{maxv}]"]
                    elif minv is not None:
                        hints = [f"≥ {minv}"]
                    elif maxv is not None:
                        hints = [f"≤ {maxv}"]
                    else:
                        hints = ["int"]
                else:
                    hints = [ftype]
        else:
            # Not editing: show section or generic prompt
            if state.sections and state.active_section_id:
                sec = state.get_active_section()
                title = f"Section S{sec.number} — type a token (e.g., 'gas', 'ec', 'filters')"
            else:
                title = "No sections — press Enter to create one"

            if token_buffer:
                token_ui = "token: " + " ".join(list(token_buffer)) + " ▎"

        # footer info
        pc = max(1, state.pdf.page_count) if state.pdf else 1
        pg = (state.pdf.page + 1) if state.pdf else 1
        zoom = int((state.pdf.zoom if state.pdf else 1.0) * 100)
        foot = f"Page {pg}/{pc}  •  Zoom {zoom}%  •  Ctrl+R reset section • Ctrl+Shift+R start over • Ctrl+O open"

        return HudModel(
            title=title,
            fields=fields,
            hints=hints,
            token_ui=token_ui,
            foot=foot,
            toasts=toasts[-3:],  # show up to last 3
            options_visual=options_visual,
            ambiguous=ambiguous,
            no_match=no_match,
        )


# -------------------------
# HUDOverlay (paint-only)
# -------------------------

class HUDOverlay(QtWidgets.QWidget):
    """Transparent overlay that paints HUD content over the canvas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self._model: Optional[HudModel] = None

    def set_model(self, model: HudModel):
        self._model = model
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        if not self._model:
            return
        p = QtGui.QPainter(self)
        p.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing)

        # Panel rect (bottom-center)
        margin = 16
        panel_w = min(self.width() - 2*margin, 980)
        panel_h = 150 if (self._model.fields or self._model.token_ui) else 100
        x = (self.width() - panel_w) // 2
        y = self.height() - panel_h - 24

        # Background
        panel_rect = QtCore.QRectF(x, y, panel_w, panel_h)
        p.setBrush(QtGui.QColor(20, 20, 24, 190))
        p.setPen(QtCore.Qt.NoPen)
        p.drawRoundedRect(panel_rect, 10, 10)

        # Text metrics
        pad = 14
        text_x = x + pad
        cur_y = y + pad

        # Title
        title = self._model.title
        p.setPen(QtGui.QColor(240, 240, 240))
        font = p.font()
        font.setPointSizeF(11.5)
        font.setBold(True)
        p.setFont(font)
        p.drawText(QtCore.QPointF(text_x, cur_y + 18), title)
        cur_y += 26

        # Fields line
        if self._model.fields:
            font.setBold(False)
            font.setPointSizeF(10.5)
            p.setFont(font)
            seg_x = text_x
            for chip in self._model.fields:
                label = f"{chip.name} = {chip.value}"
                rect = QtCore.QRectF(seg_x, cur_y, p.fontMetrics().horizontalAdvance(label) + 16, 24)
                # chip bg
                bg = QtGui.QColor(60, 60, 70, 230) if not chip.active else QtGui.QColor(90, 110, 170, 230)
                p.setBrush(bg)
                p.setPen(QtCore.Qt.NoPen)
                p.drawRoundedRect(rect, 6, 6)
                # chip text
                p.setPen(QtGui.QColor(240, 240, 240))
                p.drawText(QtCore.QPointF(rect.x() + 8, rect.y() + 17), label)
                seg_x += rect.width() + 8
            cur_y += 32

        # Hints
        if self._model.hints:
            hints = " • ".join(self._model.hints)
            p.setPen(QtGui.QColor(200, 200, 210))
            font.setPointSizeF(10)
            p.setFont(font)
            p.drawText(QtCore.QPointF(text_x, cur_y + 18), f"Hints: {hints}")
            cur_y += 24

        # Options visual line (labels with prefix underlined/bold)
        if self._model.options_visual:
            font = p.font()
            font.setPointSizeF(10.5)
            p.setFont(font)
            seg_x = text_x
            gap = 16
            for label, pref_len in self._model.options_visual:
                # split prefix/rest
                prefix = label[:pref_len]
                rest = label[pref_len:]
                # draw pill
                lab_w = p.fontMetrics().horizontalAdvance(label) + 20
                rect = QtCore.QRectF(seg_x, cur_y, lab_w, 26)
                bg = QtGui.QColor(55, 55, 65, 210)
                p.setBrush(bg)
                p.setPen(QtCore.Qt.NoPen)
                p.drawRoundedRect(rect, 6, 6)
                # text
                x0 = rect.x() + 10
                y0 = rect.y() + 18
                pen_norm = QtGui.QPen(QtGui.QColor(235, 235, 240))
                pen_emph = QtGui.QPen(QtGui.QColor(255, 255, 255))
                # prefix bold/underline
                if pref_len > 0:
                    f_b = QtGui.QFont(p.font())
                    f_b.setBold(True)
                    f_b.setUnderline(True)
                    p.setFont(f_b); p.setPen(pen_emph)
                    p.drawText(QtCore.QPointF(x0, y0), prefix)
                    w_pref = p.fontMetrics().horizontalAdvance(prefix)
                    p.setFont(font); p.setPen(pen_norm)
                    p.drawText(QtCore.QPointF(x0 + w_pref, y0), rest)
                else:
                    p.setPen(pen_norm)
                    p.drawText(QtCore.QPointF(x0, y0), label)
                seg_x += rect.width() + gap
            cur_y += 32

        # Token line
        if self._model.token_ui:
            p.setPen(QtGui.QColor(220, 220, 230))
            mono = QtGui.QFont("Monospace")
            mono.setStyleHint(QtGui.QFont.TypeWriter)
            mono.setPointSizeF(10)
            p.setFont(mono)
            p.drawText(QtCore.QPointF(text_x, cur_y + 18), self._model.token_ui)

        # Footer (page/zoom)
        p.setPen(QtGui.QColor(180, 180, 190))
        font.setPointSizeF(9.5)
        font.setBold(False)
        p.setFont(font)
        p.drawText(QtCore.QPointF(x + pad, y + panel_h - 10), self._model.foot)

        # Toasts (top-right)
        tx = self.width() - 16
        ty = 16
        for msg in self._model.toasts:
            rect = QtCore.QRectF(0, 0, min(380, self.width() - 32), 30)
            rect.moveTopRight(QtCore.QPointF(tx, ty))
            p.setBrush(QtGui.QColor(30, 120, 60, 220))
            p.setPen(QtCore.Qt.NoPen)
            p.drawRoundedRect(rect, 8, 8)
            p.setPen(QtGui.QColor(250, 250, 250))
            p.drawText(QtCore.QPointF(rect.x() + 10, rect.y() + 20), msg)
            ty += rect.height() + 8


# -------------------------
# PdfCanvas (page painter)
# -------------------------

class PdfCanvas(QtWidgets.QWidget):
    """Canvas that paints the current PDF page image from PdfIO with fit-to-width."""
    def __init__(self, pdf: PdfIO, parent=None):
        super().__init__(parent)
        self.pdf = pdf
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QtGui.QPalette.Window, QtGui.QColor(245, 245, 248))
        self.setPalette(pal)
        # Make it fill available space
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self._dim_rects_pt: list[tuple[float, float, float, float]] = []  # PDF-pt rects
        self._dim_kinds: list[str] = []   # kinds for each rect

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:
        super().resizeEvent(e)
        dpr = self.devicePixelRatioF()
        # Reserve bottom for HUD (same value you use in HUD widget)
        HUD_H = 140
        self.pdf.fit_to_frame(self.width(), self.height(), dpr, top_margin=8, bottom_margin=HUD_H)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHints(QtGui.QPainter.SmoothPixmapTransform | QtGui.QPainter.TextAntialiasing)
        img = self.pdf.qimage()
        if img.isNull():
            self._draw_placeholder(painter); return

        # draw at native logical size (Qt accounts for DPR)
        iw = int(img.width() / img.devicePixelRatio())
        ih = int(img.height() / img.devicePixelRatio())
        vw, vh = self.width(), self.height()

        x = (vw - iw) // 2
        # place near top (leave small top margin so it feels centered with HUD)
        y = 8
        painter.drawImage(QtCore.QRect(x, y, iw, ih), img)

        # --- overlays: dimension boxes ---
        if self._dim_rects_pt:
            def color_for_kind(k: str) -> QtGui.QColor:
                if k == "cabinet_height":    return QtGui.QColor(160, 90, 200)  # purple (light)
                if k == "height_base_only":  return QtGui.QColor(120, 40, 160)  # purple (dark)
                if k == "width_with_base":   return QtGui.QColor( 70,130, 220)  # blue (light)
                if k == "cabinet_width":     return QtGui.QColor( 30, 80, 180)  # blue (dark)
                return QtGui.QColor(255, 80, 0)  # fallback

            for i, rect_pt in enumerate(self._dim_rects_pt):
                kind = self._dim_kinds[i] if i < len(self._dim_kinds) else ""
                pen = QtGui.QPen(color_for_kind(kind))
                pen.setWidthF(2.0)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.NoBrush)

                r = self.pdf.rect_pdfpt_to_qrectf(rect_pt, img)
                r.translate(x, y)
                painter.drawRect(r)

    def refit(self):
        """Recompute fit-to-frame for the current page at current DPR."""
        dpr = self.devicePixelRatioF()
        HUD_H = 140  # keep in sync with resizeEvent
        self.pdf.fit_to_frame(self.width(), self.height(), dpr, top_margin=8, bottom_margin=HUD_H)
        self.update()

    def _draw_placeholder(self, p: QtGui.QPainter):
        rect = self.rect()
        p.fillRect(rect, QtGui.QColor(245, 245, 248))
        pen = QtGui.QPen(QtGui.QColor(180, 180, 190))
        p.setPen(pen)
        p.drawText(rect, QtCore.Qt.AlignCenter, "Ctrl+O to open a PDF")

    def set_dimension_rects(
        self,
        rects_pt: list[tuple[float, float, float, float]],
        kinds: Optional[list[str]] = None,
    ):
        self._dim_rects_pt = rects_pt or []
        if kinds is None:
            self._dim_kinds = [""] * len(self._dim_rects_pt)
        else:
            n = len(self._dim_rects_pt)
            self._dim_kinds = (kinds + [""] * n)[:n]  # pad/trim to match
        self.update()


# -------------------------
# UIApp (controller)
# -------------------------

class UIApp(QtWidgets.QMainWindow):
    """Main window: owns Store/Registry/PdfIO, handles keys, updates Canvas + HUD."""

    def __init__(self, on_save=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ACU PDF Annotator — Keyboard Shell (MVP)")
        self.resize(1100, 720)

        # Core
        self.registry = PluginRegistry()
        self.store = Store(registry=self.registry)
        self.pdf = PdfIO(cache_pages=12, workers=2)

        # Token builder & toasts
        self._token_buffer: Optional[str] = None
        self._toasts: List[Tuple[str, float]] = []  # (message, expires_at)
        self._toast_timer = QtCore.QTimer(self)
        self._toast_timer.setInterval(200)
        self._toast_timer.timeout.connect(self._prune_toasts)
        self._toast_timer.start()

        self._on_before_next_pdf = None

        # Save callback (can be swapped by app)
        self._on_save = on_save or self._default_save

        # --- Type-ahead field buffer ---
        this = self
        self._field_buffer: str = ""
        self._fieldbuf_timeout_ms = 4000  # configurable: 4s
        self._fieldbuf_timer = QtCore.QTimer(self)
        self._fieldbuf_timer.setSingleShot(True)
        self._fieldbuf_timer.timeout.connect(self._on_fieldbuf_timeout)
        self._pdf_list: list[str] | None = None
        self._pdf_index: int = -1

        # --- Inline length entry (HUD) ---
        self._length_input_active: bool = False
        self._length_buffer: str = ""

        # Keep latest analysis (optional)
        self._last_analysis = None

        # Central layout: PDF canvas + HUD overlay on top
        central = QtWidgets.QWidget(self)
        layout = QtWidgets.QStackedLayout(central)  # stacked to let overlay sit above
        self.canvas = PdfCanvas(self.pdf, central)
        layout.addWidget(self.canvas)
        self.setCentralWidget(central)

        # Overlay HUD
        self.hud = HUDOverlay(self)
        self.hud.setGeometry(self.centralWidget().geometry())
        self.hud.raise_()

        # Builders/helpers
        self.router = KeyRouter()
        self.prompts = PromptBuilder(self.registry)

        # --- Menu (discoverability for resets) ---
        bar = self.menuBar()
        m_file = bar.addMenu("&File")

        act_open = m_file.addAction("Open…")
        act_open.setShortcut("Ctrl+O")
        act_open.triggered.connect(self._open_pdf_dialog)

        act_reset_section = m_file.addAction("Reset Section")
        act_reset_section.setShortcut("Ctrl+R")
        act_reset_section.triggered.connect(lambda: self._dispatch(Action(Action.RESET_SECTION)))

        act_start_over = m_file.addAction("Start Over (This PDF)")
        act_start_over.setShortcut("Ctrl+Shift+R")
        act_start_over.triggered.connect(lambda: self._dispatch(Action(Action.START_OVER)))

        # Initial HUD + nice default sizing
        self._refresh_hud()
        screen = QtWidgets.QApplication.primaryScreen()
        geometry = screen.availableGeometry()
        w = int(geometry.width() * 0.8)
        h = int(geometry.height() * 0.8)
        self.resize(w, h)
        self.move((geometry.width() - w) // 2, (geometry.height() - h) // 2)

    def load_pdf_list(self, paths: list[str]):
        """Set a list of PDFs to process; open the first."""
        paths = [p for p in paths if isinstance(p, str)]
        self._pdf_list = paths if paths else None
        self._pdf_index = -1
        if self._pdf_list:
            self._open_next_pdf(initial=True)

    def _open_next_pdf(self, initial: bool = False):
        if not self._pdf_list:
            self.toast("No PDF list loaded", ttl=1.5)
            return
        if initial:
            self._pdf_index = 0
        else:
            if self.store.state.mode == Mode.FIELD_EDITING:
                # Safety: don't switch while editing a field
                self.toast("Finish current field before switching PDF", ttl=1.5)
                return
            self._pdf_index += 1
        if self._pdf_index >= len(self._pdf_list):
            self._pdf_index = len(self._pdf_list) - 1
            self.toast("Reached last PDF", ttl=1.2)
            return
        self._load_pdf_path(self._pdf_list[self._pdf_index])
        self.toast(f"PDF {self._pdf_index + 1}/{len(self._pdf_list)}", ttl=0.8)

    def _next_pdf(self):
        """Public handler for NEXT_PDF action."""
        if not self._pdf_list:
            self.toast("No folder playlist loaded", ttl=1.5)
            return
        if self.store.state.mode == Mode.FIELD_EDITING:
            # Ignore 'n' while editing a field (your requirement)
            return

        # 🔹 Let app.py stage current JSON before switching
        if callable(getattr(self, "_on_before_next_pdf", None)):
            try:
                self._on_before_next_pdf()
            except Exception as e:
                self.toast(f"Stage failed: {e}", ttl=2.0)

        self._open_next_pdf(initial=False)

    def _load_pdf_path(self, path: str):
        """Open a specific PDF and reset app state for a fresh annotation session."""
        try:
            self.pdf.open(path)
            # Reset reducer/store for a fresh document
            self.store = Store(registry=self.registry)
            # Keep reducer authoritative for nav logic but set essentials:
            self.store.state.pdf.path = path              # ⟵ ensure Exporter.filename() picks the right folder
            self.store.state.pdf.page_count = self.pdf.page_count
            self.store.state.pdf.page = 0

            # Render first view
            self.canvas.refit()

            # Clear transient UI buffers
            self._token_buffer = None
            self._field_buffer = ""
            self._fieldbuf_timer.stop()
            self._length_input_active = False
            self._length_buffer = ""

            # Initial HUD
            self._refresh_hud()

            # Analyze current page and draw dimension boxes
            analysis = analyze_page(
                pdf_path=path,
                page_index=self.store.state.pdf.page,
                dpi=150,
            )

            self._last_analysis = analysis

            rects_pt = [d.bbox_pt for d in (analysis.dimensions or [])]
            kinds    = [d.kind    for d in (analysis.dimensions or [])]
            if hasattr(self.canvas, "set_dimension_rects"):
                self.canvas.set_dimension_rects(rects_pt, kinds)

            self.canvas.update()

        except Exception as e:
            self.toast(f"Failed to open PDF: {e}", ttl=2.5)

    def _update_dimension_overlays(self):
        """Analyze the current page and paint dimension boxes on the canvas."""
        try:
            path = self.store.state.pdf.path
            page_index = self.store.state.pdf.page
            if not path:
                self.canvas.set_dimension_rects([])
                return

            analysis = analyze_page(
                pdf_path=path,
                page_index=page_index,
                dpi=150,
            )

            rects_pt = [d.bbox_pt for d in analysis.dimensions]
            kinds    = [d.kind    for d in analysis.dimensions]
            self.canvas.set_dimension_rects(rects_pt, kinds)
        except Exception as e:
            self.canvas.set_dimension_rects([])
            self.toast(f"Analyzer: {e}", ttl=2.0)

    # ---------- Type-ahead helpers ----------
    @staticmethod
    def _fold(s: str) -> str:
        """lower + strip diacritics for prefix comparison."""
        import unicodedata as _ud
        s = _ud.normalize("NFD", s)
        s = "".join(c for c in s if _ud.category(c) != "Mn")
        return s.casefold()

    def _active_enum_labels(self) -> list[str]:
        """Return option labels for active field (enum/bool), else []."""
        st = self.store.state
        if st.mode != Mode.FIELD_EDITING or not st.editing:
            return []
        spec = self.registry.get_spec(st.editing.type_id)
        seq = st.editing.field_sequence
        if not (0 <= st.editing.index < len(seq)):
            return []
        fname = seq[st.editing.index]
        fdef = spec.get("fields", {}).get(fname, {})
        ftype = fdef.get("type", "enum")
        if ftype == "bool":
            return ["Yes", "No"]
        if ftype == "enum":
            return list(dict.fromkeys(fdef.get("map", {}).values()))
        return []

    def _restart_fieldbuf_timer(self):
        if self._fieldbuf_timeout_ms > 0:
            self._fieldbuf_timer.start(self._fieldbuf_timeout_ms)

    def _on_fieldbuf_timeout(self):
        if self._field_buffer:
            self._field_buffer = ""
            self._refresh_hud()

    def _typeahead_try_commit(self):
        """Disambiguate & commit if unique (or exact label), else wait."""
        labels = self._active_enum_labels()
        fb = self._field_buffer
        if not labels:
            self._refresh_hud(); return
        if not fb:
            self._refresh_hud(); return
        F = self._fold
        fbuf = F(fb)
        exact = [L for L in labels if F(L) == fbuf]
        if exact:
            choice = exact[0]
            self._commit_choice_and_flash(choice)
            return
        matches = [L for L in labels if F(L).startswith(fbuf)]
        if len(matches) == 1:
            self._commit_choice_and_flash(matches[0])
        else:
            self._refresh_hud()

    def _handle_maybe_numeric_char(self, ch: str) -> bool:
        """
        If active field is int and `ch` is a digit, commit immediately and advance.
        Returns True if the key was consumed.
        """
        st = self.store.state
        if st.mode != Mode.FIELD_EDITING or not st.editing:
            return False
        if not ch or len(ch) != 1 or not ch.isdigit():
            return False

        spec = self.registry.get_spec(st.editing.type_id)
        seq = st.editing.field_sequence
        if not (0 <= st.editing.index < len(seq)):
            return False
        fname = seq[st.editing.index]
        fdef = spec.get("fields", {}).get(fname, {})
        if fdef.get("type", "enum") != "int":
            return False

        try:
            self.store.apply(SetFieldValue(int(ch)))
            self.store.apply(NextField())
            self._field_buffer = ""
            self._fieldbuf_timer.stop()
            self.toast(ch, ttl=0.15)
            self._refresh_hud()
            return True
        except ValueError as e:
            self.toast(str(e), ttl=1.5)
            return True  # handled (don’t feed buffer)

    def _commit_choice_and_flash(self, label: str):
        """Commit label, flash briefly, then advance to next field."""
        try:
            self.store.apply(SetFieldValue(label))
        except ValueError as e:
            self.toast(str(e), ttl=2.0)
            return
        self._field_buffer = ""  # reset on commit
        self.toast(label, ttl=0.2)
        QtCore.QTimer.singleShot(120, self._advance_after_commit)

    def _advance_after_commit(self):
        try:
            self.store.apply(NextField())
        except ValueError as e:
            self.toast(str(e), ttl=2.0)
        self._refresh_hud()

    # ------------- Toasts -------------

    def toast(self, msg: str, ttl: float = 2.0):
        self._toasts.append((msg, time.time() + ttl))
        self._refresh_hud()

    def _prune_toasts(self):
        now = time.time()
        old_len = len(self._toasts)
        self._toasts = [(m, t) for (m, t) in self._toasts if t > now]
        if len(self._toasts) != old_len:
            self._refresh_hud()

    # ------------- Save -------------

    def _default_save(self):
        when = time.time()
        self.store.apply(MarkSaved(when=when))
        self.toast("Saved JSON")

    # ------------- Events -------------

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:
        super().resizeEvent(e)
        self.hud.setGeometry(self.centralWidget().geometry())

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        # --- Inline Section Length Mode (captures keys before router) ---
        if self._length_input_active:
            key = event.key()
            text = event.text() or ""

            if key in (Qt.Key_Return, Qt.Key_Enter):
                if self._length_buffer.isdigit():
                    val = int(self._length_buffer)
                    sec = self.store.state.get_active_section()
                    if sec:
                        try:
                            self.store.apply(SetSectionLength(section_id=sec.id, length=val))
                            self.toast(f"S{sec.number} length = {val} in", ttl=1.0)
                        except Exception as e:
                            self.toast(str(e), ttl=2.0)
                else:
                    self.toast("Enter a numeric length (digits only)", ttl=1.8)

                self._length_input_active = False
                self._length_buffer = ""
                self._refresh_hud()
                event.accept()
                return

            if key == Qt.Key_Escape:
                self._length_input_active = False
                self._length_buffer = ""
                self._refresh_hud()
                event.accept()
                return

            if key == Qt.Key_Backspace:
                if self._length_buffer:
                    self._length_buffer = self._length_buffer[:-1]
                    self._refresh_hud()
                event.accept()
                return

            # accept only digits
            if text.isdigit():
                self._length_buffer += text
                self._refresh_hud()
                event.accept()
                return

            # ignore all other keys while in length mode
            event.accept()
            return

        # --- normal flow if not in length mode ---
        try:
            action = self.router.route(self.store.state, event, token_active=self._token_buffer is not None)
            self._dispatch(action)
        except Exception as ex:
            self.toast(str(ex), ttl=2.5)
        finally:
            event.accept()

    # ------------- Dispatch -------------

    def _dispatch(self, action: Action):
        kind = action.kind
        pay = action.payload

        if kind == Action.NOOP:
            return

        if kind == Action.OPEN_PDF:
            self._open_pdf_dialog()
            return

        if kind == Action.NEW_SECTION:
            # Autoname S{n+1}. Length left None for now.
            number = (self.store.state.sections[-1].number + 1) if self.store.state.sections else 1
            name = f"S{number}"
            self.store.apply(NewSection(name=name, length=None))
            self._token_buffer = None
            self._field_buffer = ""
            self._fieldbuf_timer.stop()
            self.toast(f"New section: {name}", ttl=1.2)

            # Start inline length entry (HUD-driven)
            self._length_input_active = True
            self._length_buffer = ""
            self._refresh_hud()

        elif kind == Action.TOKEN_APPEND:
            ch = str(pay)
            if self._token_buffer is None:
                self._token_buffer = ch
            else:
                self._token_buffer += ch

        elif kind == Action.TOKEN_BACKSPACE:
            if self._token_buffer:
                self._token_buffer = self._token_buffer[:-1]
                if not self._token_buffer:
                    self._token_buffer = None

        elif kind == Action.TOKEN_SUBMIT:
            tok = (self._token_buffer or "").strip()
            if not tok:
                return
            try:
                self.store.apply(StartComponent(token=tok))
                self._token_buffer = None
                self._field_buffer = ""  # new draft → reset field buffer
                self._fieldbuf_timer.stop()
            except ValueError:
                self.toast(f"Unknown component '{tok}'", ttl=2.0)

        elif kind == Action.TOKEN_CLEAR:
            self._token_buffer = None

        elif kind == Action.FIELDBUF_APPEND:
            ch = str(pay)
            # Numeric one-tap commit
            if self._handle_maybe_numeric_char(ch):
                return
            # Otherwise, type-ahead for enum/bool
            self._field_buffer += ch
            self._restart_fieldbuf_timer()
            self._typeahead_try_commit()

        elif kind == Action.FIELDBUF_BACKSPACE:
            if self._field_buffer:
                self._field_buffer = self._field_buffer[:-1]
            self._restart_fieldbuf_timer()
            self._typeahead_try_commit()

        elif kind == Action.FIELDBUF_CLEAR:
            if self._field_buffer:
                self._field_buffer = ""
                self._fieldbuf_timer.stop()
                self._refresh_hud()

        elif kind == Action.NEXT_FIELD:
            try:
                self.store.apply(NextField())
                self._field_buffer = ""  # reset on advance
                self._fieldbuf_timer.stop()
            except ValueError as e:
                self.toast(str(e), ttl=2.0)

        elif kind == Action.PREV_FIELD:
            self.store.apply(PrevField())
            self._field_buffer = ""  # reset on backtrack
            self._fieldbuf_timer.stop()

        elif kind == Action.CANCEL_DRAFT:
            self.store.apply(CancelDraft())
            self.toast("Canceled draft", ttl=1.2)
            self._field_buffer = ""
            self._fieldbuf_timer.stop()

        elif kind == Action.SAVE:
            self._on_save()

        elif kind == Action.NAV_PAGE:
            delta = int(pay)
            self.store.apply(NavPage(delta))
            self.pdf.nav(delta)
            self.canvas.update()
            page = self.store.state.pdf.page + 1
            total = max(1, self.store.state.pdf.page_count)
            self.toast(f"Page {page}/{total}", ttl=0.8)

            # Update overlays
            self._update_dimension_overlays()
            self.canvas.update()

        elif kind == Action.SET_ZOOM:
            zoom = float(pay)
            self.store.apply(SetZoom(zoom))
            self.pdf.set_zoom(self.store.state.pdf.zoom)
            self.canvas.refit()
            self.toast(f"Zoom {int(self.store.state.pdf.zoom*100)}%", ttl=0.8)

        elif kind == Action.UNDO:
            self.store.undo()
            self._field_buffer = ""  # state potentially different → clear
            self._fieldbuf_timer.stop()

        elif kind == Action.REDO:
            self.store.redo()
            self._field_buffer = ""
            self._fieldbuf_timer.stop()

        elif kind == Action.PREV_SECTION:
            from state import PrevSection  # lazy import
            self.store.apply(PrevSection())
            self.toast(f"Section S{self.store.state.get_active_section().number}", ttl=0.8)

        elif kind == Action.NEXT_SECTION:
            from state import NextSection
            self.store.apply(NextSection())
            self.toast(f"Section S{self.store.state.get_active_section().number}", ttl=0.8)

        # >>> advance to the next PDF in playlist (only fires when not editing)
        elif kind == Action.NEXT_PDF:
            self._next_pdf()

        # --- Reset active section (Ctrl+R): clears components AND length, then re-prompt length ---
        elif kind == Action.RESET_SECTION:
            sec = self.store.state.get_active_section()
            if not sec:
                self.toast("No active section", ttl=1.5); self._refresh_hud(); return
            ans = QtWidgets.QMessageBox.question(
                self, "Reset Section",
                f"Clear Section S{sec.number} (components and length)?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No
            )
            if ans != QtWidgets.QMessageBox.Yes:
                self._refresh_hud(); return
            try:
                # Reducer version with no args:
                self.store.apply(ResetSection())
                # Immediately re-prompt for length
                self._token_buffer = None
                self._field_buffer = ""
                self._fieldbuf_timer.stop()
                self._length_input_active = True
                self._length_buffer = ""
                sec2 = self.store.state.get_active_section()
                if sec2:
                    self.toast(f"Section S{sec2.number} reset — enter length", ttl=1.4)
                else:
                    self.toast("Section reset — enter length", ttl=1.4)
            except Exception as e:
                self.toast(str(e), ttl=2.0)

        # --- Start over for this PDF (Ctrl+Shift+R) ---
        elif kind == Action.START_OVER:
            if not self.store.state.pdf.path:
                self.toast("No PDF loaded", ttl=1.5); self._refresh_hud(); return
            ans = QtWidgets.QMessageBox.question(
                self, "Start Over",
                "This will clear ALL sections/components for this PDF.\nContinue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No
            )
            if ans != QtWidgets.QMessageBox.Yes:
                self._refresh_hud(); return
            path = self.store.state.pdf.path
            page = self.store.state.pdf.page
            # Fresh store but preserve current PDF context
            self.store = Store(registry=self.registry)
            self.store.state.pdf.path = path
            self.store.state.pdf.page_count = self.pdf.page_count
            self.store.state.pdf.page = page
            # Clear transient UI buffers
            self._token_buffer = None
            self._field_buffer = ""
            self._fieldbuf_timer.stop()
            self._length_input_active = False
            self._length_buffer = ""
            # Re-render & overlays
            self.canvas.refit()
            self._update_dimension_overlays()
            self.toast("Cleared annotations for this PDF", ttl=1.2)

        self._refresh_hud()

    # ------------- HUD refresh -------------

    def _refresh_hud(self):
        msgs = [m for (m, t) in self._toasts if t > time.time()]
        model = self.prompts.build(self.store.state, self._token_buffer, msgs, field_buffer=self._field_buffer)

        # Inline length HUD overlay
        if self._length_input_active:
            sec = self.store.state.get_active_section()
            secnum = sec.number if sec else "?"
            model.title = f"Section S{secnum} — enter length (inches)"
            model.hints = ["Type digits • Backspace to edit • Enter to confirm • Esc to skip"]
            disp = self._length_buffer if self._length_buffer else ""
            model.token_ui = f"length: {disp}▎"

        self.hud.set_model(model)
        self.canvas.update()

    # ------------- PDF open -------------

    def _open_pdf_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open PDF", "", "PDF files (*.pdf);;All files (*)"
        )
        if not path:
            return
        # Opening via dialog cancels any existing playlist and just opens this file
        self._pdf_list = [path]
        self._pdf_index = 0
        self._load_pdf_path(path)
        self.toast("PDF loaded", ttl=1.0)


# -------------------------
# Entrypoint
# -------------------------

def main():
    app = QtWidgets.QApplication(sys.argv)
    win = UIApp()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
