"""Marker for tests that read the downloaded public datasets. A clean checkout has none (data is fetched, never shipped):
those tests skip with a reason instead of failing. Fetch with `scripts/fetch_data.sh dev` and `ants data build`."""
from pathlib import Path

import pytest

RAW_ETTH1 = Path(__file__).resolve().parents[1] / "data" / "raw" / "etth1" / "ETTh1.csv"
needs_raw = pytest.mark.skipif(not RAW_ETTH1.exists(), reason="needs the public datasets: scripts/fetch_data.sh dev")
