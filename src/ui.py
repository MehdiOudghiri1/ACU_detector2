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
from types import SimpleNamespace
from pathlib import Path





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
    SET_FIELD_VALUE = "SET_FIELD_VALUE"  # kept for backward-compat (not used by type-ahead)
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

    # Resets
    START_OVER = "START_OVER"           # Ctrl+Shift+R → reset all annotations for this PDF
    RESET_SECTION = "RESET_SECTION"
    RESET_ALL = "RESET_ALL"
    PREV_PDF = "PREV_PDF"      # ⟵ add this


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
    # NEW: visual cue when section length not set
    awaiting_length: bool


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
        foot = f"Page {pg}/{pc}  •  Zoom {zoom}%  •  Ctrl+O to open PDF"

        # awaiting length?
        awaiting_length = False
        if state.sections and state.active_section_id:
            sec = state.get_active_section()
            awaiting_length = (sec is not None and sec.length is None)

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
            awaiting_length=awaiting_length,
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
        # UI scale: +15% for the HUD and toasts
        self._scale = 1.15

    def set_model(self, model: HudModel):
        self._model = model
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        if not self._model:
            return
        p = QtGui.QPainter(self)
        p.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing)

        S = self._scale

        # Panel rect (bottom-center)
        margin = 16
        panel_w = min(self.width() - 2*margin, int(980 * S))  # widen slightly with scale
        base_h = 150 if (self._model.fields or self._model.token_ui) else 100
        panel_h = int(base_h * S)
        x = (self.width() - panel_w) // 2
        y = self.height() - panel_h - int(24 * S)

        # Background (light green if awaiting length)
        panel_rect = QtCore.QRectF(x, y, panel_w, panel_h)
        if self._model.awaiting_length:
            # light green glass
            bg = QtGui.QColor(30, 90, 200, 180)
        else:
            bg = QtGui.QColor(20, 20, 24, 190)
        p.setBrush(bg)
        p.setPen(QtCore.Qt.NoPen)
        p.drawRoundedRect(panel_rect, 10, 10)

        # Text metrics
        pad = int(14 * S)
        text_x = x + pad
        cur_y = y + pad

        # Title
        title = self._model.title
        p.setPen(QtGui.QColor(240, 240, 240))
        font = p.font()
        font.setPointSizeF(11.5 * S)
        font.setBold(True)
        p.setFont(font)
        p.drawText(QtCore.QPointF(text_x, cur_y + int(18 * S)), title)
        cur_y += int(26 * S)

        # Fields line
        if self._model.fields:
            font.setBold(False)
            font.setPointSizeF(10.5 * S)
            p.setFont(font)
            seg_x = text_x
            for chip in self._model.fields:
                label = f"{chip.name} = {chip.value}"
                rect = QtCore.QRectF(seg_x, cur_y, p.fontMetrics().horizontalAdvance(label) + int(16 * S), int(24 * S))
                # chip bg
                bg_chip = QtGui.QColor(60, 60, 70, 230) if not chip.active else QtGui.QColor(90, 110, 170, 230)
                p.setBrush(bg_chip)
                p.setPen(QtCore.Qt.NoPen)
                p.drawRoundedRect(rect, 6, 6)
                # chip text
                p.setPen(QtGui.QColor(240, 240, 240))
                p.drawText(QtCore.QPointF(rect.x() + int(8 * S), rect.y() + int(17 * S)), label)
                seg_x += rect.width() + int(8 * S)
            cur_y += int(32 * S)

        # Hints
        if self._model.hints:
            hints = " • ".join(self._model.hints)
            p.setPen(QtGui.QColor(200, 200, 210))
            font.setPointSizeF(10 * S)
            p.setFont(font)
            p.drawText(QtCore.QPointF(text_x, cur_y + int(18 * S)), f"Hints: {hints}")
            cur_y += int(24 * S)

        # Options visual line (labels with prefix underlined/bold)
        if self._model.options_visual:
            font = p.font()
            font.setPointSizeF(10.5 * S)
            p.setFont(font)
            seg_x = text_x
            gap = int(16 * S)
            for label, pref_len in self._model.options_visual:
                # split prefix/rest
                prefix = label[:pref_len]
                rest = label[pref_len:]
                # draw pill
                lab_w = p.fontMetrics().horizontalAdvance(label) + int(20 * S)
                rect = QtCore.QRectF(seg_x, cur_y, lab_w, int(26 * S))
                bg = QtGui.QColor(55, 55, 65, 210)
                p.setBrush(bg)
                p.setPen(QtCore.Qt.NoPen)
                p.drawRoundedRect(rect, 6, 6)
                # text
                x0 = rect.x() + int(10 * S)
                y0 = rect.y() + int(18 * S)
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
            cur_y += int(32 * S)

        # Token line
        if self._model.token_ui:
            p.setPen(QtGui.QColor(220, 220, 230))
            mono = QtGui.QFont("Monospace")
            mono.setStyleHint(QtGui.QFont.TypeWriter)
            mono.setPointSizeF(10 * S)
            p.setFont(mono)
            p.drawText(QtCore.QPointF(text_x, cur_y + int(18 * S)), self._model.token_ui)

        # Footer (page/zoom)
        p.setPen(QtGui.QColor(180, 180, 190))
        font.setPointSizeF(9.5 * S)
        font.setBold(False)
        p.setFont(font)
        p.drawText(QtCore.QPointF(x + pad, y + panel_h - int(10 * S)), self._model.foot)

        # Toasts (top-right) — 15% larger
        tx = self.width() - int(16 * S)
        ty = int(16 * S)
        toast_h = int(30 * S)
        font_toast = QtGui.QFont(p.font())
        font_toast.setPointSizeF(10.5 * S)
        for msg in self._model.toasts:
            rect = QtCore.QRectF(0, 0, min(int(380 * S), self.width() - int(32 * S)), toast_h)
            rect.moveTopRight(QtCore.QPointF(tx, ty))
            p.setBrush(QtGui.QColor(30, 120, 60, 220))   # blue
            p.setPen(QtCore.Qt.NoPen)
            p.drawRoundedRect(rect, 8, 8)
            p.setPen(QtGui.QColor(250, 250, 250))
            p.setFont(font_toast)
            p.drawText(QtCore.QPointF(rect.x() + int(10 * S), rect.y() + int(20 * S)), msg)
            ty += rect.height() + int(8 * S)


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
        # inside KeyRouter.route, under "Global shortcuts"
        if mods & QtCore.Qt.ControlModifier:
            # Full restart: Ctrl+R
            if key == QtCore.Qt.Key_R and not (mods & QtCore.Qt.ShiftModifier):
                return Action(Action.START_OVER)
            # Section-only reset: Ctrl+Shift+R
            if (mods & QtCore.Qt.ShiftModifier) and key == QtCore.Qt.Key_R:
                return Action(Action.RESET_SECTION)

                # (If you ever add Ctrl+R variations, handle them here.)
            if key in (QtCore.Qt.Key_O,):
                return Action(Action.OPEN_PDF)
            if key in (QtCore.Qt.Key_S,):
                return Action(Action.SAVE)
            if key in (QtCore.Qt.Key_P,):
                return Action(Action.NAV_PAGE, -1)
            if key in (QtCore.Qt.Key_N,):
                return Action(Action.NAV_PAGE, +1)
            # Zoom in aliases: Ctrl + (+) OR (=) OR Up Arrow OR ]
            if key in (
                QtCore.Qt.Key_Plus,
                QtCore.Qt.Key_Equal,
                QtCore.Qt.Key_Up,
                QtCore.Qt.Key_BracketRight,
            ):
                cur = state.pdf.zoom if state.pdf else 1.0
                return Action(Action.SET_ZOOM, min(cur * 1.1, 4.0))

            # Zoom out aliases: Ctrl + (-) OR Down Arrow OR [
            if key in (
                QtCore.Qt.Key_Minus,
                QtCore.Qt.Key_Down,
                QtCore.Qt.Key_BracketLeft,
            ):
                cur = state.pdf.zoom if state.pdf else 1.0
                return Action(Action.SET_ZOOM, max(cur / 1.1, 0.25))

            # Reset to 100%
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


        if key == QtCore.Qt.Key_P and not (mods & QtCore.Qt.ControlModifier) and not token_active:
            return Action(Action.PREV_PDF)

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
        # --- Inline Indoor/Outdoor chooser ---
        self._io_choice_active: bool = False
        self._io_current: str = "Indoor"        # default preselection
        self._io_after_new_section: bool = False

        # --- Tiny header showing current PDF name + page ---
        self._titlebar = QtWidgets.QToolBar(self)
        self._titlebar.setMovable(False)
        self._titlebar.setFloatable(False)
        self._titlebar.setIconSize(QtCore.QSize(16, 16))
        self._titlebar.setFixedHeight(26)  # small strip
        self._titlebar.setStyleSheet("QToolBar{background: #f5f5f8; border: none;}")

        self._title_label = QtWidgets.QLabel("— no PDF —")
        self._title_label.setStyleSheet("color:#555; font-size:11px;")
        self._title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)

        self._titlebar.addWidget(self._title_label)
        self._titlebar.addWidget(spacer)
        self.addToolBar(QtCore.Qt.TopToolBarArea, self._titlebar)


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

        # Start Over (this PDF) → now Ctrl+R
        act_start_over = m_file.addAction("Start Over (This PDF)")
        act_start_over.setShortcut("Ctrl+R")
        act_start_over.triggered.connect(lambda: self._dispatch(Action(Action.START_OVER)))

        # Reset Section → now Ctrl+Shift+R
        act_reset_section = m_file.addAction("Reset Section")
        act_reset_section.setShortcut("Ctrl+Shift+R")
        act_reset_section.triggered.connect(lambda: self._dispatch(Action(Action.RESET_SECTION)))


        # Initial HUD + nice default sizing
        self._refresh_hud()
        screen = QtWidgets.QApplication.primaryScreen()
        geometry = screen.availableGeometry()
        w = int(geometry.width() * 0.8)
        h = int(geometry.height() * 0.8)
        self.resize(w, h)
        self.move((geometry.width() - w) // 2, (geometry.height() - h) // 2)
    
    def _ensure_meta(self):
        st = self.store.state
        if not hasattr(st, "meta") or st.meta is None:
            st.meta = SimpleNamespace(indoor_outdoor=None)
        elif not hasattr(st.meta, "indoor_outdoor"):
            st.meta.indoor_outdoor = None


    # add near other helpers in UIApp
    def _apply_dimensions_to_meta(self, analysis):
        """Copy detected dimensions into state.meta so Exporter picks them up."""
        from types import SimpleNamespace
        st = self.store.state
        meta = getattr(st, "meta", None) or SimpleNamespace()

        def grab(kind: str):
            dims = getattr(analysis, "dimensions", None) or []
            for d in dims:
                if getattr(d, "kind", None) == kind and getattr(d, "value", None) is not None:
                    return d.value
            return None

        # Map detector kinds → export/meta attribute names
        setattr(meta, "width_with_base",  grab("width_with_base"))
        setattr(meta, "base_height",      grab("height_base_only"))   # Exporter expects "Height (base only)" ← base_height
        setattr(meta, "cabinet_height",   grab("cabinet_height"))
        setattr(meta, "cabinet_width",    grab("cabinet_width"))

        st.meta = meta

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

    def _update_header(self):
        path = getattr(self.store.state.pdf, "path", None)
        if path:
            name = Path(path).name
            pg = (self.store.state.pdf.page + 1) if self.store.state.pdf else 1
            pc = max(1, self.store.state.pdf.page_count) if self.store.state.pdf else 1
            self._title_label.setText(f"{name}  ·  Page {pg}/{pc}")
        else:
            self._title_label.setText("— no PDF —")


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

    def _prev_pdf(self):
        if not self._pdf_list:
            self.toast("No folder playlist loaded", ttl=1.5)
            return
        if self.store.state.mode == Mode.FIELD_EDITING:
            # Ignore 'p' while editing a field
            return

        # Let app.py stage current JSON before switching (same hook as next)
        if callable(getattr(self, "_on_before_next_pdf", None)):
            try:
                self._on_before_next_pdf()
            except Exception as e:
                self.toast(f"Stage failed: {e}", ttl=2.0)

        if self._pdf_index <= 0:
            self._pdf_index = 0
            self.toast("At first PDF", ttl=1.2)
            return

        self._pdf_index -= 1
        self._load_pdf_path(self._pdf_list[self._pdf_index])
        self.toast(f"PDF {self._pdf_index + 1}/{len(self._pdf_list)}", ttl=0.8)


    def _load_pdf_path(self, path: str):
        """Open a specific PDF and reset app state for a fresh annotation session."""
        try:
            self.pdf.open(path)
            # Reset reducer/store for a fresh document
            self.store = Store(registry=self.registry)
            self._ensure_meta()
            self._io_choice_active: bool = False
            self._io_current: str = "Indoor"  # default cursor
            if getattr(self.store.state.meta, "indoor_outdoor", None) is None:
                self._io_choice_active = True

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
            self._apply_dimensions_to_meta(analysis)


            rects_pt = [d.bbox_pt for d in (analysis.dimensions or [])]
            kinds    = [d.kind    for d in (analysis.dimensions or [])]
            if hasattr(self.canvas, "set_dimension_rects"):
                self.canvas.set_dimension_rects(rects_pt, kinds)

            self.canvas.update()
            self._update_header()


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

            self._apply_dimensions_to_meta(analysis)
            self._last_analysis = analysis


            rects_pt = [d.bbox_pt for d in analysis.dimensions]
            kinds    = [d.kind    for d in analysis.dimensions]
            self.canvas.set_dimension_rects(rects_pt, kinds)
        except Exception as e:
            self.canvas.set_dimension_rects([])
            self.toast(f"Analyzer: {e}", ttl=2.0)

    def _set_indoor_outdoor(self, value: str):
        from types import SimpleNamespace
        st = self.store.state
        meta = getattr(st, "meta", None) or SimpleNamespace()
        meta.indoor_outdoor = value  # "Indoor" | "Outdoor"
        st.meta = meta
        st.dirty = True
        self.toast(f"Unit location: {value}", ttl=1.2)


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
        # --- Inline Indoor/Outdoor chooser (shown at startup before any sections) ---
        if self._io_choice_active:
            key = event.key()
            text = (event.text() or "").lower()

            # Toggle with arrows or I/O keys
            if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Tab) or text in ("i", "o"):
                if text == "i":
                    self._io_current = "Indoor"
                elif text == "o":
                    self._io_current = "Outdoor"
                else:
                    # flip on arrows/tab
                    self._io_current = "Outdoor" if self._io_current == "Indoor" else "Indoor"
                self._refresh_hud()
                event.accept()
                return

            # Confirm with Enter
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._ensure_meta()
                self.store.state.meta.indoor_outdoor = self._io_current
                self._io_choice_active = False
                self.toast(f"Installation: {self._io_current}", ttl=1.2)
                self._refresh_hud()
                event.accept()
                return

            # (Optional) Escape: do nothing—force the user to choose first
            event.accept()
            return

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

        # --- normal flow if not in any inline mode ---
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
            # Require Indoor/Outdoor before the very first section
            if (
                not self.store.state.sections
                and not getattr(getattr(self.store.state, "meta", None), "indoor_outdoor", None)
            ):
                # Pop the inline chooser; we'll resume NEW_SECTION after the user confirms
                self._io_choice_active = True
                self._io_current = "Indoor"      # default preselection
                self._io_after_new_section = True
                self._refresh_hud()
                return

            # Create the section now
            number = (self.store.state.sections[-1].number + 1) if self.store.state.sections else 1
            name = f"S{number}"
            self.store.apply(NewSection(name=name, length=None))

            # Clear transient buffers
            self._token_buffer = None
            self._field_buffer = ""
            self._fieldbuf_timer.stop()

            self.toast(f"New section: {name}", ttl=1.2)

            # Immediately prompt for Section Length (inline HUD)
            self._length_input_active = True
            self._length_buffer = ""
            self._refresh_hud()
            return


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
            self._update_header()


        elif kind == Action.SET_ZOOM:
            zoom = float(pay)
            self.store.apply(SetZoom(zoom))
            self.pdf.set_zoom(self.store.state.pdf.zoom)  # keep manual zoom
            self.canvas.update()                          # repaint only (no fit_to_frame)
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
        elif kind == Action.PREV_PDF:
            self._prev_pdf()

        # --- Reset active section (Ctrl+R) ---
        elif kind == Action.RESET_SECTION:
            sec = self.store.state.get_active_section()
            if not sec:
                self.toast("No active section", ttl=1.5); self._refresh_hud(); return
            ans = QtWidgets.QMessageBox.question(
                self, "Reset Section",
                f"Clear all components in Section S{sec.number}? (Length will be kept)",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No
            )
            if ans != QtWidgets.QMessageBox.Yes:
                self._refresh_hud(); return
            from state import ResetSection
            try:
                self.store.apply(ResetSection(section_id=sec.id, clear_length=False))
                self.toast(f"Section S{sec.number} cleared", ttl=1.2)
            except Exception as e:
                self.toast(str(e), ttl=2.0)

        # --- Start over for this PDF (Ctrl+Shift+R) ---
        elif kind == Action.START_OVER:
            if not self.store.state.pdf.path:
                self.toast("No PDF loaded", ttl=1.5); self._refresh_hud(); return

            ans = QtWidgets.QMessageBox.question(
                self, "Start Over",
                "This will clear ALL sections/components for this PDF and restart from the beginning.\nContinue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No
            )
            if ans != QtWidgets.QMessageBox.Yes:
                self._refresh_hud(); return

            path = self.store.state.pdf.path
            page = self.store.state.pdf.page

            # Fresh store (wipes sections, lengths, meta—including Indoor/Outdoor)
            self.store = Store(registry=self.registry)
            self.store.state.pdf.path = path
            self.store.state.pdf.page_count = self.pdf.page_count
            self.store.state.pdf.page = page

            # Clear transient UI buffers
            self._token_buffer = None
            self._field_buffer = ""
            self._length_input_active = False
            self._length_buffer = ""

            # 🔹 Re-show the Indoor/Outdoor inline chooser
            self._io_choice_active = True
            self._io_current = "Indoor"  # default highlight

            # UI refresh
            self.canvas.refit()
            self._update_dimension_overlays()
            self.toast("Project reset — choose Indoor/Outdoor to begin", ttl=1.6)
            self._refresh_hud()
            return


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

        # Inline Indoor/Outdoor chooser HUD
        if self._io_choice_active:
            model.title = "Select unit location"
            model.hints = ["Press I for Indoor • O for Outdoor • Enter to confirm"]
            model.token_ui = f"Indoor/Outdoor: [{self._io_current}] ▎"

        # Inline Indoor/Outdoor HUD overlay
        if self._io_choice_active:
            model.title = "Select installation type"
            model.hints = ["Use ←/→ or I / O to switch • Enter to confirm"]
            model.fields = []  # no chips while choosing
            model.token_ui = f"installation: {self._io_current} ▎"
            # Visually highlight the current choice using the pill row
            model.options_visual = [
                ("Indoor", len("Indoor") if self._io_current == "Indoor" else 0),
                ("Outdoor", len("Outdoor") if self._io_current == "Outdoor" else 0),
            ]

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
