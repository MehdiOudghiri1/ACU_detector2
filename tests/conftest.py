# tests/conftest.py
import sys
from pathlib import Path
import pytest

# Make "src" importable
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from registry import PluginRegistry
from state import Store

@pytest.fixture(scope="session")
def registry():
    # Loads built-in specs from registry/specs.py
    return PluginRegistry()

@pytest.fixture
def store(registry):
    # Fresh store per test
    st = Store(registry=registry)
    # Provide a PDF context so Exporter.filename() can place output next to it
    st.state.pdf.path = str(ROOT / "samples" / "AHU-23.pdf")
    st.state.pdf.page_count = 3
    st.state.pdf.page = 0
    return st
