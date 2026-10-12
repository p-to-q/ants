"""One version, three files: the npm package, the Python package and the citation file must agree."""
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_package_json_pyproject_and_citation_carry_the_same_version():
    npm = json.loads((ROOT / "package.json").read_text())["version"]
    py = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    cff = re.search(r"^version:\s*['\"]?([^'\"\s]+)", (ROOT / "CITATION.cff").read_text(), re.M).group(1)
    assert npm == py == cff, (npm, py, cff)


def test_npm_package_is_named_ants_lab_and_ships_the_licence_files():
    pkg = json.loads((ROOT / "package.json").read_text())
    assert pkg["name"] == "ants-lab" and pkg["bin"] == {"ants": "bin/ants.js"}
    for needed in ("LICENSE", "NOTICE", "bin/", "src/", "contracts/"):
        assert needed in pkg["files"], needed
    assert not any(f.startswith(("internal", "runs")) for f in pkg["files"])
