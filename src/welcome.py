# src/welcome.py
from __future__ import annotations
from PySide6 import QtCore, QtGui, QtWidgets
from pathlib import Path
from typing import List, Optional, Tuple

class WelcomeDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ACU Studio — Welcome")
        self.setModal(True)

        # ----- Size: ~60% of the available screen, centered -----
        screen = QtWidgets.QApplication.primaryScreen()
        geom = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1400, 900)
        w = int(geom.width() * 0.60)
        h = int(geom.height() * 0.60)
        self.resize(w, h)
        self.move(
            geom.x() + (geom.width() - w) // 2,
            geom.y() + (geom.height() - h) // 2
        )

        # --------- Brand header (hero) ----------
        header = QtWidgets.QFrame(self)
        header.setObjectName("hero")
        header.setFixedHeight(150)
        hero_layout = QtWidgets.QVBoxLayout(header)
        hero_layout.setContentsMargins(28, 20, 28, 20)
        hero_layout.setSpacing(8)

        title = QtWidgets.QLabel("ACU Studio")
        title.setObjectName("title")
        subtitle = QtWidgets.QLabel("PDF Annotation & Structured Export")
        subtitle.setObjectName("subtitle")
        hero_layout.addWidget(title)
        hero_layout.addWidget(subtitle)
        hero_layout.addStretch()

        # --------- Main body ----------
        body = QtWidgets.QWidget(self)
        form = QtWidgets.QFormLayout(body)
        form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(14)

        self.folder_edit = QtWidgets.QLineEdit()
        self.file_edit = QtWidgets.QLineEdit()
        self.recursive_chk = QtWidgets.QCheckBox("Include subfolders")

        pick_folder_btn = QtWidgets.QPushButton("Browse…")
        pick_file_btn = QtWidgets.QPushButton("Browse…")
        pick_folder_btn.clicked.connect(self._choose_folder)
        pick_file_btn.clicked.connect(self._choose_file)

        # Inputs rows
        folder_row = QtWidgets.QHBoxLayout()
        folder_row.setSpacing(10)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(pick_folder_btn)

        file_row = QtWidgets.QHBoxLayout()
        file_row.setSpacing(10)
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(pick_file_btn)

        form.addRow(self._label("Root folder:"), self._wrap(folder_row))
        form.addRow(self._label("Specific file (optional):"), self._wrap(file_row))
        form.addRow("", self.recursive_chk)

        # --------- Footer (copyright) ----------
        footer = QtWidgets.QLabel("© 2025 ACU Studio • v0.1.0 — All rights reserved.")
        footer.setAlignment(QtCore.Qt.AlignCenter)
        footer.setObjectName("footer")

        # --------- Buttons ----------
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        # --------- Page layout ----------
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(14)
        outer.addWidget(header)
        outer.addWidget(body, 1)
        outer.addWidget(btns)
        outer.addWidget(footer)

        # --------- High-contrast + larger scale ----------
        self.setStyleSheet("""
            QDialog {
                background: #0f1115;
                color: #f5f7fa;
                font-size: 15px;
            }
            #hero {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #1b2434, stop:1 #223047);
                border-radius: 12px;
            }
            QLabel#title { font-size: 34px; font-weight: 800; color: #ffffff; letter-spacing: 0.5px; }
            QLabel#subtitle { font-size: 16px; color: #e3e9f2; }
            QLabel.formLabel { color: #e8ecf5; font-weight: 700; font-size: 15px; }
            QLabel#footer { color: #c7d0dc; font-size: 13px; padding-top: 6px; }
            QLineEdit {
                background: #141a22;
                color: #f8fbff;
                selection-background-color: #2a7de1;
                border: 1px solid #334155;
                padding: 12px;
                border-radius: 10px;
                font-size: 15px;
            }
            QLineEdit:focus { border-color: #3b82f6; }
            QPushButton {
                background: #3b82f6;
                color: #ffffff;
                padding: 10px 18px;
                border-radius: 10px;
                font-weight: 700;
                font-size: 15px;
                min-height: 44px;
            }
            QPushButton:hover { background: #4f8ff9; }
            QPushButton:disabled { background: #314765; color: #c9d3df; }
            QDialogButtonBox QPushButton {
                background: #2563eb;
                font-weight: 800;
                padding: 10px 22px;
                border-radius: 10px;
                min-height: 46px;
                font-size: 15px;
            }
            QDialogButtonBox QPushButton:hover { background: #2f6ff0; }
            QCheckBox { color: #f1f5fb; font-weight: 600; font-size: 15px; }
            QCheckBox::indicator { width: 20px; height: 20px; }
        """)

    # --- Helpers for form labels / wrapping layouts ---
    def _label(self, text: str) -> QtWidgets.QLabel:
        lab = QtWidgets.QLabel(text)
        lab.setObjectName("formLabel")
        lab.setProperty("class", "formLabel")
        return lab

    def _wrap(self, layout: QtWidgets.QLayout) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setLayout(layout)
        return w

    # --- public getters used by app.py ---
    def selected_folder(self) -> str:
        return self.folder_edit.text().strip()

    def selected_file(self) -> str:
        return self.file_edit.text().strip()

    def is_recursive(self) -> bool:
        return self.recursive_chk.isChecked()

    # ---------- Larger, non-native dialogs ----------
    def _choose_folder(self):
        dlg = QtWidgets.QFileDialog(self, "Select root folder")
        dlg.setFileMode(QtWidgets.QFileDialog.Directory)
        dlg.setOption(QtWidgets.QFileDialog.ShowDirsOnly, True)
        dlg.setOption(QtWidgets.QFileDialog.DontUseNativeDialog, True)
        self._enlarge_popup(dlg, baseline_fraction=0.50, scale=1.25)
        self._scale_popup_contents(dlg, font_pt=16, icon_px=28, row_px=34)
        if dlg.exec():
            paths = dlg.selectedFiles()
            if paths:
                self.folder_edit.setText(paths[0])

    def _choose_file(self):
        dlg = QtWidgets.QFileDialog(self, "Select PDF")
        dlg.setFileMode(QtWidgets.QFileDialog.ExistingFile)
        dlg.setNameFilter("PDF files (*.pdf)")
        dlg.setOption(QtWidgets.QFileDialog.DontUseNativeDialog, True)
        self._enlarge_popup(dlg, baseline_fraction=0.50, scale=1.25)
        self._scale_popup_contents(dlg, font_pt=16, icon_px=28, row_px=34)
        if dlg.exec():
            paths = dlg.selectedFiles()
            if paths:
                self.file_edit.setText(paths[0])

    def _scale_popup_contents(self, dlg: QtWidgets.QFileDialog, *, font_pt: int, icon_px: int, row_px: int):
        # 1) Font for the dialog and its children
        f = dlg.font()
        f.setPointSize(font_pt)
        dlg.setFont(f)

        # 2) Icon size and row height for all list/tree views inside the dialog
        for view in dlg.findChildren(QtWidgets.QAbstractItemView):
            view.setIconSize(QtCore.QSize(icon_px, icon_px))
            # Some styles respect grid size for row height (esp. icon mode)
            if hasattr(view, "setGridSize"):
                g = view.gridSize()
                if not g.isValid():
                    g = QtCore.QSize(200, row_px)
                else:
                    g.setHeight(max(g.height(), row_px))
                view.setGridSize(g)

        # 3) Header sections (name/date/size) font/padding
        for header in dlg.findChildren(QtWidgets.QHeaderView):
            header.setMinimumSectionSize(120)
            header.setDefaultSectionSize(max(header.defaultSectionSize(), 180))

        # 4) Stylesheet fallback to enforce row height & font where needed
        dlg.setStyleSheet(f"""
            QTreeView, QListView {{
                font-size: {font_pt}px;
            }}
            QTreeView::item, QListView::item {{
                height: {row_px}px;
            }}
            QHeaderView::section {{
                font-size: {max(font_pt-1, 12)}px;
                padding: 6px 10px;
            }}
            QPushButton {{
                font-size: {font_pt}px;
                min-height: {max(row_px, 36)}px;
                padding: 8px 14px;
            }}
        """)

    def _enlarge_popup(self, dlg: QtWidgets.QFileDialog, baseline_fraction: float = 0.50, scale: float = 1.25):
        """
        Size the dialog to (baseline_fraction * screen) then multiply by scale.
        Example: 0.50 * 1.25 = 0.625 → ~62.5% of available screen in each dimension.
        """
        screen = QtWidgets.QApplication.primaryScreen()
        geom = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1400, 900)
        base_w = int(geom.width() * baseline_fraction)
        base_h = int(geom.height() * baseline_fraction)
        w = int(base_w * scale)
        h = int(base_h * scale)
        # Apply and center relative to the welcome dialog's screen
        dlg.resize(w, h)
        # center on the same screen as the parent dialog
        center_x = geom.x() + (geom.width() - w) // 2
        center_y = geom.y() + (geom.height() - h) // 2
        dlg.move(center_x, center_y)

    # ---------- NEW: minimal API for app.py ----------
    def get_selection(self) -> Tuple[Optional[Path], List[Path]]:
        """
        Return (folder, pdfs) for the playlist:
          - If a specific file is chosen, return that file only; folder is its parent
            (unless a folder was also typed/selected, in which case that value is used).
          - Else if only a folder is chosen, return all PDFs in it (recursive if checked).
          - Else return (None, []) meaning user didn't pick anything valid.
        """
        file_txt = self.selected_file()
        folder_txt = self.selected_folder()
        recursive = self.is_recursive()

        # Specific file wins if present
        if file_txt:
            file_path = Path(file_txt).expanduser().resolve()
            if not file_path.is_file() or file_path.suffix.lower() != ".pdf":
                return (None, [])
            # Prefer explicit folder if provided, otherwise use file's parent
            folder_path = Path(folder_txt).expanduser().resolve() if folder_txt else file_path.parent
            return (folder_path if folder_path.exists() else file_path.parent, [file_path])

        # Else, folder mode
        if folder_txt:
            folder_path = Path(folder_txt).expanduser().resolve()
            if not folder_path.is_dir():
                return (None, [])
            pdfs = self._gather_pdfs(folder_path, recursive=recursive)
            return (folder_path, pdfs)

        # Nothing selected
        return (None, [])

    def _gather_pdfs(self, folder: Path, *, recursive: bool) -> List[Path]:
        if recursive:
            return sorted((p for p in folder.rglob("*.pdf") if p.is_file()),
                          key=lambda x: x.as_posix().lower())
        return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"),
                      key=lambda x: x.as_posix().lower())
