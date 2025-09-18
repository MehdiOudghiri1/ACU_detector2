# src/ui.py
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Optional, List, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from registry import PluginRegistry
from state import (
    Store, Mode,
    NewSection, StartComponent, SetFieldValue, NextField, PrevField,
    CancelDraft, NavPage, SetZoom, MarkSaved,
)
from pdfio import PdfIO


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


        # --- Mode-specific ---
        if state.mode == Mode.FIELD_EDITING:
            if key == QtCore.Qt.Key_Tab and not (mods & QtCore.Qt.ShiftModifier):
                return Action(Action.NEXT_FIELD)
            if key == QtCore.Qt.Key_Backtab or (key == QtCore.Qt.Key_Tab and (mods & QtCore.Qt.ShiftModifier)):
                return Action(Action.PREV_FIELD)
            if key == QtCore.Qt.Key_Backspace or key == QtCore.Qt.Key_Escape:
                return Action(Action.CANCEL_DRAFT)
            # Character input for current field
            if text and not (mods & QtCore.Qt.ControlModifier):
                ch = text.strip()
                if ch:
                    return Action(Action.SET_FIELD_VALUE, ch)
            return Action(Action.NOOP)

        # SECTION_ACTIVE (and IDLE behaves the same for MVP)
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
    hints: List[str]
    token_ui: Optional[str]
    foot: str  # e.g., filename / page / zoom
    toasts: List[str]


class PromptBuilder:
    def __init__(self, registry: PluginRegistry):
        self.registry = registry

    def build(self, state, token_buffer: Optional[str], toasts: List[str]) -> HudModel:
        # Title + fields + hints
        title = ""
        fields: List[FieldChip] = []
        hints: List[str] = []
        token_ui: Optional[str] = None

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
                if ftype == "enum":
                    m = fdef.get("map", {})
                    short_keys = [k for k in m.keys() if len(k) == 1]
                    if short_keys:
                        hints = ["/".join(short_keys).upper()]
                    else:
                        hints = ["/".join(sorted(set(m.values())))]
                elif ftype == "bool":
                    hints = ["Y/N"]
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

        return HudModel(
            title=title,
            fields=fields,
            hints=hints,
            token_ui=token_ui,
            foot=foot,
            toasts=toasts[-3:],  # show up to last 3
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
        panel_w = min(self.width() - 2*margin, 900)
        panel_h = 130 if (self._model.fields or self._model.token_ui) else 90
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

# ui.py - PdfCanvas

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

    def _draw_placeholder(self, p: QtGui.QPainter):
        rect = self.rect()
        p.fillRect(rect, QtGui.QColor(245, 245, 248))
        pen = QtGui.QPen(QtGui.QColor(180, 180, 190))
        p.setPen(pen)
        p.drawText(rect, QtCore.Qt.AlignCenter, "Ctrl+O to open a PDF")


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

        # Save callback (can be swapped by app)
        self._on_save = on_save or self._default_save

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

        # Initial HUD
        self._refresh_hud()
        screen = QtWidgets.QApplication.primaryScreen()
        geometry = screen.availableGeometry()

        w = int(geometry.width() * 0.8)
        h = int(geometry.height() * 0.8)

        self.resize(w, h)
        self.move(
            (geometry.width() - w) // 2,
            (geometry.height() - h) // 2
        )


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
            self.toast(f"New section: {name}", ttl=1.2)

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
            except ValueError:
                self.toast(f"Unknown component '{tok}'", ttl=2.0)

        elif kind == Action.TOKEN_CLEAR:
            self._token_buffer = None

        elif kind == Action.SET_FIELD_VALUE:
            try:
                self.store.apply(SetFieldValue(pay))
            except ValueError as e:
                self.toast(str(e), ttl=2.2)

        elif kind == Action.NEXT_FIELD:
            try:
                self.store.apply(NextField())
            except ValueError as e:
                self.toast(str(e), ttl=2.0)

        elif kind == Action.PREV_FIELD:
            self.store.apply(PrevField())

        elif kind == Action.CANCEL_DRAFT:
            self.store.apply(CancelDraft())
            self.toast("Canceled draft", ttl=1.2)

        elif kind == Action.SAVE:
            self._on_save()

        elif kind == Action.NAV_PAGE:
            delta = int(pay)
            # Update state then PdfIO (keep both in sync)
            self.store.apply(NavPage(delta))
            self.pdf.nav(delta)
            self.canvas.update()
            page = self.store.state.pdf.page + 1
            total = max(1, self.store.state.pdf.page_count)
            self.toast(f"Page {page}/{total}", ttl=0.8)

        elif kind == Action.SET_ZOOM:
            zoom = float(pay)
            self.store.apply(SetZoom(zoom))
            self.pdf.set_zoom(self.store.state.pdf.zoom)
            self.canvas.update()
            self.toast(f"Zoom {int(self.store.state.pdf.zoom*100)}%", ttl=0.8)

        elif kind == Action.UNDO:
            self.store.undo()

        elif kind == Action.REDO:
            self.store.redo()

        elif kind == Action.PREV_SECTION:
            from state import PrevSection  # local import if not already at top
            self.store.apply(PrevSection())
            self.toast(f"Section S{self.store.state.get_active_section().number}", ttl=0.8)

        elif kind == Action.NEXT_SECTION:
            from state import NextSection
            self.store.apply(NextSection())
            self.toast(f"Section S{self.store.state.get_active_section().number}", ttl=0.8)


        self._refresh_hud()

    # ------------- HUD refresh -------------

    def _refresh_hud(self):
        msgs = [m for (m, t) in self._toasts if t > time.time()]
        model = self.prompts.build(self.store.state, self._token_buffer, msgs)
        self.hud.set_model(model)
        self.canvas.update()

    # ------------- PDF open -------------

    def _open_pdf_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open PDF", "", "PDF files (*.pdf);;All files (*)"
        )
        if not path:
            return
        try:
            self.pdf.open(path)
            # Keep reducer authoritative for nav logic:
            # We don't have a command for page_count; set directly (one-time side effect).
            self.store.state.pdf.page_count = self.pdf.page_count  # noqa: direct state set (practical)
            self.store.state.pdf.page = 0
            self.canvas.update()
            self.toast("PDF loaded", ttl=1.0)
        except Exception as e:
            self.toast(f"Failed to open PDF: {e}", ttl=2.5)


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
