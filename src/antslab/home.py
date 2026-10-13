"""Where ants keeps its own state when it is installed rather than run from a checkout, and the small config that onboarding writes.

    ~/.ants/            (or $ANTS_HOME)
        config.toml     what `ants` with no arguments should use: contract, workdir, arm
        data/           catalog files, sources, pinned manifest, raw/ and processed/
        runs/default/   the default workdir (ledger, per-trial files)
        llm.env         optional endpoints for the language-model arms (mode 600)

Standard library only.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("ANTS_HOME") or Path.home() / ".ants").expanduser()


def data_dir() -> Path:
    return home() / "data"


def config_path() -> Path:
    return home() / "config.toml"


def load_config() -> dict:
    p = config_path()
    if not p.exists():
        return {}
    try:
        with open(p, "rb") as f:
            return tomllib.load(f)
    except OSError:
        return {}
    except tomllib.TOMLDecodeError as e:
        import sys
        print(f"ants: ignoring {p}: {e}", file=sys.stderr)      # never silently: the defaults it falls back to may point at a different ledger
        return {}


def _q(v) -> str:
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_config(cfg: dict) -> Path:
    """flat sections of strings and numbers: enough for our own config, and it round-trips through tomllib"""
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# written by `ants onboard`; edit freely"]
    for section, values in cfg.items():
        lines.append(f"\n[{section}]")
        for k, v in values.items():
            lines.append(f"{k} = {v if isinstance(v, (int, float)) and not isinstance(v, bool) else (str(v).lower() if isinstance(v, bool) else _q(v))}")
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(p)
    return p


def package_root() -> Path | None:
    """The directory with contracts/ and data/ files shipped with ants: $ANTS_ROOT (npm launcher), else the source tree of an editable install."""
    r = os.environ.get("ANTS_ROOT")
    if r and (Path(r) / "contracts").is_dir():
        return Path(r)
    here = Path(__file__).resolve().parents[2]
    return here if (here / "contracts").is_dir() and (here / "data" / "catalog.toml").exists() else None
