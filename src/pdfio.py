# src/pdfio.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional
import threading
import fitz  # PyMuPDF
from PySide6 import QtGui

@dataclass
class _RenderCache:
    # key: (page_index, scale_bucket, dpr_bucket)
    images: Dict[Tuple[int, float, float], QtGui.QImage] = field(default_factory=dict)
    order: list[Tuple[int, float, float]] = field(default_factory=list)
    capacity: int = 12

    def get(self, key):
        return self.images.get(key)

    def put(self, key, img: QtGui.QImage):
        if key in self.images:
            # move to end
            self.order.remove(key)
            self.order.append(key)
            self.images[key] = img
            return
        self.images[key] = img
        self.order.append(key)
        while len(self.order) > self.capacity:
            k = self.order.pop(0)
            self.images.pop(k, None)

class PdfIO:
    """
    Fast PDF renderer with fit-to-width at device DPR.
    - open(path)
    - qimage(): last rendered image for current page
    - nav(delta), set_page(i)
    - set_zoom(z)  # explicit zoom; disables auto-fit
    - enable_fit_width(True/False)
    - fit_to_width(view_px, dpr): re-render using scale computed from page rect
    """
    def __init__(self, cache_pages: int = 12, workers: int = 2):
        self._doc: Optional[fitz.Document] = None
        self.page_count = 0
        self.page = 0
        self.zoom = 1.0
        self._cache = _RenderCache(capacity=cache_pages)
        self._lock = threading.Lock()
        self._last: Optional[QtGui.QImage] = None
        self._fit_width_enabled = True
        self._max_scale = 8.0  # allow high DPI
        self._page_width_pts: Optional[float] = None  # points at 72dpi for current page

    # ------------- lifecycle -------------
    def open(self, path: str):
        self.close()
        self._doc = fitz.open(path)
        self.page_count = len(self._doc)
        self.page = 0
        self.zoom = 1.0
        self._page_width_pts = None
        self._last = None

    def close(self):
        if self._doc:
            self._doc.close()
        self._doc = None
        self.page_count = 0
        self.page = 0
        self._cache = _RenderCache(capacity=self._cache.capacity)
        self._last = None
        self._page_width_pts = None

    # ------------- controls -------------
    def nav(self, delta: int):
        if not self._doc or self.page_count == 0:
            return
        self.page = max(0, min(self.page + delta, self.page_count - 1))
        self._page_width_pts = None
        self._last = None  # force re-render

    def set_page(self, i: int):
        if not self._doc:
            return
        self.page = max(0, min(i, self.page_count - 1))
        self._page_width_pts = None
        self._last = None

    def set_zoom(self, z: float):
        """Manual zoom: disable fit-width and render at this zoom (1.0 = 72dpi scale)."""
        self._fit_width_enabled = False
        self.zoom = max(0.25, min(z, self._max_scale))
        self._last = None  # will re-render on next qimage()

    def enable_fit_width(self, enabled: bool):
        self._fit_width_enabled = bool(enabled)
        self._last = None

    # ------------- rendering -------------
    def qimage(self) -> QtGui.QImage:
        """Return the last rendered image, rendering if necessary."""
        if self._last is not None:
            return self._last
        # If not rendered yet, render at current zoom (non-fit path) as fallback.
        self._last = self._render_by_zoom(self.page, self.zoom, dpr=1.0)
        return self._last

    def fit_to_width(self, view_px: int, dpr: float):
        """Re-render current page to exactly fit the given view width at device DPR."""
        if not self._doc or self.page_count == 0:
            return
        self._ensure_page_width_pts()
        if not self._page_width_pts or self._page_width_pts <= 0:
            return
        # compute scale: pixels / points (72dpi)
        target_pixels = max(100, int(view_px * max(1.0, dpr)))
        scale = target_pixels / float(self._page_width_pts)
        scale = max(0.25, min(scale, self._max_scale))
        self.zoom = scale  # keep in sync for UI footer
        self._fit_width_enabled = True
        self._last = self._render_by_zoom(self.page, scale, dpr=dpr)

    # ------------- internals -------------
    def _ensure_page_width_pts(self):
        if self._page_width_pts is not None:
            return
        if not self._doc:
            return
        page = self._doc.load_page(self.page)
        rect = page.rect  # points
        self._page_width_pts = rect.width

    def _render_by_zoom(self, page_index: int, scale: float, dpr: float) -> QtGui.QImage:
        """Render using PyMuPDF at given scale (72dpi base) and attach DPR for sharp painting."""
        if not self._doc:
            return QtGui.QImage()

        # bucket to avoid overcaching nearly-identical scales
        scale_b = round(scale, 3)
        dpr_b = round(max(1.0, dpr), 2)
        key = (page_index, scale_b, dpr_b)

        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached

        page = self._doc.load_page(page_index)
        mat = fitz.Matrix(scale_b, scale_b)
        pix = page.get_pixmap(matrix=mat, alpha=False)  # bg white; set alpha=True if you need transparency

        # build QImage with DPR for HiDPI
        img = QtGui.QImage(pix.samples, pix.width, pix.height, pix.stride, QtGui.QImage.Format_RGB888)
        img.setDevicePixelRatio(dpr_b)
        img = img.copy()  # detach from pixmap buffer

        with self._lock:
            self._cache.put(key, img)

        return img

        # pdfio.py (add this to PdfIO)

    def fit_to_frame(self, view_w_px: int, view_h_px: int, dpr: float, top_margin: int = 8, bottom_margin: int = 140):
        """
        Render current page to fully fit inside the given view rect (no cropping).
        Respects device pixel ratio for crispness.
        """
        if not self._doc or self.page_count == 0:
            return
        page = self._doc.load_page(self.page)
        rect = page.rect  # points @ 72dpi
        page_w_pts, page_h_pts = rect.width, rect.height
        if page_w_pts <= 0 or page_h_pts <= 0:
            return

        # available logical size (leave room for HUD if you keep it at bottom)
        avail_w = max(50, view_w_px)
        avail_h = max(50, view_h_px - top_margin - bottom_margin)

        # convert to *device* pixels
        dpw = avail_w * max(1.0, dpr)
        dph = avail_h * max(1.0, dpr)

        # scale to fit both dimensions
        sx = dpw / page_w_pts
        sy = dph / page_h_pts
        scale = max(0.25, min(sx, sy, self._max_scale))

        self.zoom = scale
        self._fit_width_enabled = False  # explicitly fit-page mode
        self._last = self._render_by_zoom(self.page, scale, dpr=dpr)

