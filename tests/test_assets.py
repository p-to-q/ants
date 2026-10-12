"""The logo's published numbers (centring, radius, spacing) and the figures' basic health, checked on the generated files."""
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
LOGO = ASSETS / "logo"
NS = {"s": "http://www.w3.org/2000/svg"}


def _build():
    for script in ("make_figures.py", "make_logo.py"):
        subprocess.run([sys.executable, str(ASSETS / script)], check=True, capture_output=True)


def test_every_svg_is_well_formed():
    _build()
    files = sorted(ASSETS.glob("*.svg")) + sorted(LOGO.glob("*.svg"))
    assert len(files) >= 13
    for f in files:
        ET.parse(f)


def test_tile_is_22_5_percent_rounded_and_the_mark_is_60_percent_wide_and_centred():
    _build()
    root = ET.parse(LOGO / "tile.svg").getroot()
    rect = root.find("s:rect", NS)
    assert abs(float(rect.get("rx")) - 512 * 0.225) < 0.1
    g = root.find("s:g", NS)
    m = re.match(r"translate\(([-\d.]+),([-\d.]+)\) scale\(([\d.]+)\)", g.get("transform"))
    tx, ty, k = map(float, m.groups())
    x0, y0, x1, y1 = 7.0, 10.7, 63.3, 50.0                        # the mark's bounding box in its own coordinates
    left, right, top, bottom = tx + k * x0, tx + k * x1, ty + k * y0, ty + k * y1
    assert abs((right - left) - 512 * 0.60) < 0.5                  # 60% of the side
    assert abs((left + right) / 2 - 256) < 0.1 and abs((top + bottom) / 2 - 256) < 0.1     # centred both ways


def test_wordmark_plate_has_2x_padding_and_a_22_5_percent_radius():
    _build()
    root = ET.parse(LOGO / "wordmark-plate.svg").getroot()
    rect = root.find("s:rect", NS)
    h = float(rect.get("height"))
    assert abs(float(rect.get("rx")) - h * 0.225) < 0.05
    x_unit = 14.0
    assert abs(h - (39.3 + 4 * x_unit)) < 0.05                       # lockup height plus 2X above and below


def test_logo_and_banner_draw_the_same_mark():
    _build()
    from importlib import util
    spec = util.spec_from_file_location("mf", ASSETS / "make_figures.py")
    mf = util.module_from_spec(spec)
    spec.loader.exec_module(mf)
    shapes = mf.mark_shapes()
    assert shapes in (LOGO / "mark.svg").read_text() and shapes in (ASSETS / "banner.svg").read_text()


def test_logo_readme_links_resolve():
    for target in re.findall(r"\]\(([^)#\s]+)\)", (LOGO / "README.md").read_text()):
        if not target.startswith("http"):
            assert (LOGO / target).resolve().exists(), target
