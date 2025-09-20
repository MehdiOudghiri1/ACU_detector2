from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Tuple, List

import numpy as np
import pdfplumber
from PIL import Image, ImageDraw, ImageTk
import tkinter as tk


# ---------- Utilities ----------

def top_bottom_bands(arr: np.ndarray) -> Tuple[int, int, int, int]:
    """
    Given a numpy image array (H x W [x C]), return:
      (y1, y2, y3, y4)
    where:
      y1, y2 = start/end rows of top 0–25% band
      y3, y4 = start/end rows of bottom 75–100% band
    """
    H = arr.shape[0]
    y1, y2 = 0, int(round(0.25 * H))
    y3, y4 = int(round(0.75 * H)), H
    return y1, y2, y3, y4


import numpy as np
from typing import Optional, Tuple

def find_white_band(
    gray: np.ndarray,
    min_height: int = 10,
    white_threshold: int = 255,
    frac_low: float = 0.2,
    frac_top: float = 0.2,
) -> Optional[Tuple[int, int]]:
    """
    Find the tallest horizontal band (rows) where every pixel is "white",
    restricted to the vertical window between [frac_low, 1 - frac_top].

    Parameters
    ----------
    gray : np.ndarray
        2D grayscale image (H x W).
    min_height : int
        Minimum band height (rows).
    white_threshold : int
        Pixels >= this value are considered white.
    frac_low : float
        Fraction of the top of the image to ignore (0..1).
    frac_top : float
        Fraction of the bottom of the image to ignore (0..1).

    Returns
    -------
    (y1, y2) inclusive-exclusive row indices of the band, or None.
    """
    H, W = gray.shape[:2]

    # Compute the vertical search window
    y_start = int(round(H * frac_low))
    y_end = int(round(H * (1.0 - frac_top)))
    if y_end <= y_start:
        return None

    best_y1, best_y2 = None, None
    cur_start = None

    for y in range(y_start, y_end):
        if np.all(gray[y] >= white_threshold):
            if cur_start is None:
                cur_start = y
        else:
            if cur_start is not None:
                if y - cur_start >= min_height:
                    if best_y1 is None or (y - cur_start) > (best_y2 - best_y1):
                        best_y1, best_y2 = cur_start, y
                cur_start = None

    if cur_start is not None and y_end - cur_start >= min_height:
        if best_y1 is None or (y_end - cur_start) > (best_y2 - best_y1):
            best_y1, best_y2 = cur_start, y_end

    if best_y1 is None:
        return None
    return best_y1, best_y2


def render_white_band_image(
    pdf_path: str,
    page_index: int = 0,
    resolution: int = 150,
    min_height: int = 10,
    white_threshold: int = 255,
    alpha: float = 0.3,
    color: Tuple[int, int, int] = (255, 0, 0),  # red by default
) -> Tuple[Image.Image, Optional[Tuple[int, int]]]:
    """
    Render a PDF page and overlay the detected white band.
    Returns (PIL Image RGBA, (y1,y2) or None).
    """
    if not (0.0 <= alpha <= 1.0):
        raise ValueError("alpha must be in [0, 1]")

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_index]
        page_img = page.to_image(resolution=resolution)

        gray = np.array(page_img.original.convert("L"))
        H, W = gray.shape

        band = find_white_band(gray, min_height=min_height, white_threshold=white_threshold)

        base = page_img.original.convert("RGBA")
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        if band is not None:
            y1, y2 = band
            a = int(round(255 * alpha))
            ImageDraw.Draw(overlay).rectangle([0, y1, W, y2], fill=(color[0], color[1], color[2], a))

        out = Image.alpha_composite(base, overlay)
        return out, band


# ---------- Tkinter App ----------

class WhiteBandApp:
    def __init__(
        self,
        pdfs: List[Path],
        page_index: int = 0,
        dpi: int = 150,
        min_height: int = 10,
        white_threshold: int = 255,
        alpha: float = 0.3,
        color: Tuple[int, int, int] = (255, 0, 0),
    ):
        self.pdfs = pdfs
        self.i = 0
        self.page_index = page_index
        self.dpi = dpi
        self.min_height = min_height
        self.white_threshold = white_threshold
        self.alpha = alpha
        self.color = color

        self.root = tk.Tk()
        self.root.title("White Band Viewer — n: next, p: prev, space: next, q: quit")
        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # key bindings
        self.root.bind("<KeyPress-n>", self.next_pdf)
        self.root.bind("<KeyPress-p>", self.prev_pdf)
        self.root.bind("<space>", self.next_pdf)
        self.root.bind("<KeyPress-q>", self.quit)

        self._photo = None
        self._render_current()

    def _render_current(self):
        pdf_path = self.pdfs[self.i]
        img, band = render_white_band_image(
            str(pdf_path),
            page_index=self.page_index,
            resolution=self.dpi,
            min_height=self.min_height,
            white_threshold=self.white_threshold,
            alpha=self.alpha,
            color=self.color,
        )

        # window title with band info
        self.root.title(
            f"[{self.i+1}/{len(self.pdfs)}] {pdf_path.name} — band={band} — n/p/q"
        )

        self._photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.config(width=img.width, height=img.height)
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)

    # controls
    def next_pdf(self, event=None):
        if self.i < len(self.pdfs) - 1:
            self.i += 1
            self._render_current()
        else:
            self.root.bell()

    def prev_pdf(self, event=None):
        if self.i > 0:
            self.i -= 1
            self._render_current()
        else:
            self.root.bell()

    def quit(self, event=None):
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ---------- CLI ----------

def _collect_pdfs(root: Path, recursive: bool) -> List[Path]:
    pattern = "**/*.pdf" if recursive else "*.pdf"
    return sorted(root.glob(pattern), key=lambda p: (p.parent.as_posix(), p.name.lower()))


def main() -> int:
    ap = argparse.ArgumentParser(description="White band detector (Tkinter app)")
    ap.add_argument("folder", help="Folder containing PDFs")
    ap.add_argument("-r", "--recursive", action="store_true", help="Scan subfolders recursively")
    ap.add_argument("-p", "--page", type=int, default=0, help="Page index (0-based)")
    ap.add_argument("-d", "--dpi", type=int, default=150, help="Raster DPI (default: 150)")
    ap.add_argument("--alpha", type=float, default=0.3, help="Overlay transparency in [0,1]")
    ap.add_argument("--min-height", type=int, default=10, help="Minimum band height in rows")
    ap.add_argument("--white-threshold", type=int, default=255, help="Row is white if all px >= threshold")
    ap.add_argument("--color", default="255,0,0", help="Overlay RGB as 'R,G,B' (default red)")
    args = ap.parse_args()

    try:
        color = tuple(int(x) for x in args.color.split(","))
        if len(color) != 3 or any(not (0 <= v <= 255) for v in color):
            raise ValueError
    except Exception:
        print("Invalid --color. Use 'R,G,B' with 0..255.")
        return 2

    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        print(f"Not a folder: {root}")
        return 2

    pdfs = _collect_pdfs(root, args.recursive)
    if not pdfs:
        print(f"No PDFs found under {root}")
        return 1

    app = WhiteBandApp(
        pdfs=pdfs,
        page_index=args.page,
        dpi=args.dpi,
        min_height=args.min_height,
        white_threshold=args.white_threshold,
        alpha=args.alpha,
        color=color,  # type: ignore[arg-type]
    )
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
