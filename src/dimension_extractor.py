# src/dimension_extractor_api.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict

import numpy as np
import pdfplumber
import re

# ---------- Public datatypes ----------

BBoxPT = Tuple[float, float, float, float]  # (x0, top, x1, bottom) in PDF points (top-left origin)

@dataclass(frozen=True)
class WhiteBand:
    y_top_pt: float
    y_bottom_pt: float
    height_pt: float

@dataclass(frozen=True)
class DimensionBox:
    kind: str                     # "width_with_base" | "cabinet_width" | "cabinet_height" | "height_base_only"
    bbox_pt: BBoxPT               # in PDF points
    text_raw: str
    value: Optional[int]          # reversed-digits int or None

@dataclass(frozen=True)
class PageAnalysis:
    white_band: Optional[WhiteBand]
    dimensions: List[DimensionBox]

# ---------- Internals (private) ----------

NumberBox = Tuple[float, float, float, float, str]  # (x0, top, x1, bottom, text)

def _is_number_like(txt: str) -> bool:
    return bool(re.search(r"\d", txt.strip()))

def _reverse_digits_value(text: str) -> Optional[int]:
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    try:
        return int(digits[::-1])
    except ValueError:
        return None

def _render_gray(page: pdfplumber.page.Page, dpi: int) -> tuple[np.ndarray, float, int]:
    img = page.to_image(resolution=dpi).original.convert("L")
    gray = np.array(img)
    Hpx = gray.shape[0]
    scale = dpi / 72.0  # px per pt
    return gray, scale, Hpx

def _find_white_band_px(
    gray: np.ndarray,
    lower_frac: float,
    upper_frac: float,
    min_height_px: int,
    max_height_frac: float,
    white_threshold: int,
) -> Optional[Tuple[int, int]]:
    H, _ = gray.shape
    y0, y1 = int(H * lower_frac), int(H * upper_frac)
    if y1 <= y0:
        return None
    # “fully white”: row is white if all pixels >= threshold
    row_white = (gray[y0:y1] >= white_threshold).all(axis=1)

    best_len = 0
    best_start = None
    cur_len = 0
    cur_start = 0
    for i, ok in enumerate(row_white):
        if ok:
            if cur_len == 0:
                cur_start = i
            cur_len += 1
        else:
            if cur_len > best_len:
                best_len, best_start = cur_len, cur_start
            cur_len = 0
    if cur_len > best_len:
        best_len, best_start = cur_len, cur_start

    if best_start is None or best_len < min_height_px:
        return None

    max_len = int(H * max_height_frac)
    best_len = min(best_len, max_len)

    y_upper = y0 + best_start
    y_lower = y_upper + best_len
    return (y_upper, y_lower)

# ---------- Public API ----------

def analyze_page(
    pdf_path: str,
    page_index: int = 0,
    *,
    dpi: int = 150,
    # white-band search window (fractions of page height)
    search_lower_frac: float = 0.20,
    search_upper_frac: float = 0.80,
    min_band_height_px: int = 15,
    max_band_height_frac: float = 0.15,
    white_threshold: int = 255,
) -> PageAnalysis:
    """
    Analyze a page and return white band + dimension boxes, all in PDF points (top-left origin).

    Call this from the UI (no drawing inside). The UI can map PDF points to screen via PdfIO.
    """
    with pdfplumber.open(pdf_path) as pdf:
        if page_index >= len(pdf.pages):
            return PageAnalysis(white_band=None, dimensions=[])

        page = pdf.pages[page_index]

        # 1) Extract numeric tokens (already in PDF points)
        words = page.extract_words() or []
        tokens: List[NumberBox] = [
            (w["x0"], w["top"], w["x1"], w["bottom"], w["text"])
            for w in words if _is_number_like(w.get("text", ""))
        ]

        # 2) Detect white band in pixels, convert to PDF points (no mirroring!)
        gray, scale, Hpx = _render_gray(page, dpi)
        band_px = _find_white_band_px(
            gray,
            lower_frac=search_lower_frac,
            upper_frac=search_upper_frac,
            min_height_px=min_band_height_px,
            max_height_frac=max_band_height_frac,
            white_threshold=white_threshold,
        )
        white_band: Optional[WhiteBand] = None
        if band_px:
            y_upper_px, y_lower_px = band_px
            # px -> pt (top-left origin in pdfplumber)
            y_upper_pt = y_upper_px / scale
            y_lower_pt = y_lower_px / scale
            white_band = WhiteBand(
                y_top_pt=y_upper_pt,
                y_bottom_pt=y_lower_pt,
                height_pt=abs(y_lower_pt - y_upper_pt),
            )

        # 3) Classify tokens relative to band & select the four dimension fields
        def center_pt(box: NumberBox) -> tuple[float, float]:
            x0, t, x1, b, _ = box
            return ((x0 + x1) / 2.0, (t + b) / 2.0)

        dims: Dict[str, DimensionBox] = {}
        if tokens:
            # Split tokens using band (if present). Top-left origin: smaller y = higher
            above: List[NumberBox] = []
            below: List[NumberBox] = []
            if white_band:
                for box in tokens:
                    _, cy = center_pt(box)
                    if cy < white_band.y_top_pt:
                        above.append(box)
                    elif cy > white_band.y_bottom_pt:
                        below.append(box)
            else:
                # No band → treat all as "below" so we still try to pick widths
                below = tokens[:]

            # Heuristics (preserving your earlier logic):
            # - HEIGHTS from "below" group (leftmost two by y)
            if below:
                below_sorted_x = sorted(below, key=lambda b: (b[0], b[1]))
                # Cabinet height: first by top (smallest top) within leftmost x-group
                leftmost_x = below_sorted_x[0][0]
                group = [b for b in below if abs(b[0] - leftmost_x) <= 2.0]  # small x tolerance in pts
                group_sorted_y = sorted(group, key=lambda b: b[1])
                if group_sorted_y:
                    b0 = group_sorted_y[0]
                    dims["cabinet_height"] = DimensionBox(
                        kind="cabinet_height",
                        bbox_pt=b0[:4],
                        text_raw=b0[4],
                        value=_reverse_digits_value(b0[4]),
                    )
                    if len(group_sorted_y) >= 2:
                        b1 = group_sorted_y[1]
                        dims["height_base_only"] = DimensionBox(
                            kind="height_base_only",
                            bbox_pt=b1[:4],
                            text_raw=b1[4],
                            value=_reverse_digits_value(b1[4]),
                        )

            # - WIDTHS from "above" group (leftmost is width_with_base; nearest in same-x group is cabinet_width)
                        # --- WIDTHS from "above" tokens using adjacent X-groups ---
            if above:
                # helper: cluster by x-center with ~2px tolerance
                def group_by_xcenter(boxes: List[NumberBox], tol_px: float = 2.0):
                    tol_pt = tol_px / max(1e-6, scale)  # px -> pt
                    items = [(*b, ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)) for b in boxes]  # append (cx,cy)
                    items.sort(key=lambda it: it[-1][0])  # by cx
                    groups: List[List[tuple]] = []
                    cur: List[tuple] = []
                    cur_cx: Optional[float] = None
                    for it in items:
                        cx, cy = it[-1]
                        if cur and cur_cx is not None and abs(cx - cur_cx) > tol_pt:
                            groups.append(cur)
                            cur = [it]
                            cur_cx = cx
                        else:
                            cur.append(it)
                            cur_cx = cx if cur_cx is None else (cur_cx + cx) / 2.0
                    if cur:
                        groups.append(cur)
                    return groups

                groups = group_by_xcenter(above)
                if groups:
                    # width_with_base = leftmost token in the leftmost group (by x, then by y)
                    left_group = groups[0]
                    left_group_sorted = sorted(left_group, key=lambda it: (it[0], it[1]))  # by x0 then top
                    wwb_it = left_group_sorted[0]
                    wwb_box = wwb_it[:5]  # NumberBox
                    _, _, _, _, _ = wwb_box
                    cx_w, cy_w = wwb_it[-1]

                    dims["width_with_base"] = DimensionBox(
                        kind="width_with_base",
                        bbox_pt=wwb_box[:4],
                        text_raw=wwb_box[4],
                        value=_reverse_digits_value(wwb_box[4]),
                    )

                    # cabinet_width = token in the NEXT x-group (adjacent to the right) with closest Y to wwb
                    if len(groups) >= 2:
                        next_group = groups[1]
                        # pick by minimal |Δy|
                        def dy(it): return abs(it[-1][1] - cy_w)
                        cabw_it = min(next_group, key=dy)
                        cabw_box = cabw_it[:5]
                        dims["cabinet_width"] = DimensionBox(
                            kind="cabinet_width",
                            bbox_pt=cabw_box[:4],
                            text_raw=cabw_box[4],
                            value=_reverse_digits_value(cabw_box[4]),
                        )


        return PageAnalysis(
            white_band=white_band,
            dimensions=list(dims.values())
        )
