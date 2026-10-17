"""Read-only pre/post-deploy checks.  Never creates, migrates, checkpoints or writes any file.

Usage:
    ants preflight [--series NAME | --workdir W --arm A] [--base DIR] [--json] [--strict]

Each check returns (level, message) where level is OK / WARN / FAIL / INFO.
Exit 0 if no FAIL (--strict: also no WARN), 1 otherwise.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import stat
import sys
import time
from pathlib import Path


# ---- individual checks -------------------------------------------------------

def check_runtime() -> list[tuple[str, str]]:
    """ants version, Python >= 3.11, numpy/scipy importable, interpreter path."""
    from .update import current_version
    out: list[tuple[str, str]] = []
    out.append(("OK", f"ants {current_version()}, Python {platform.python_version()}, {sys.executable}"))
    if sys.version_info < (3, 11):
        out.append(("FAIL", f"Python >= 3.11 required, got {platform.python_version()}"))
    for pkg in ("numpy", "scipy"):
        try:
            __import__(pkg)
            out.append(("OK", f"{pkg} importable"))
        except ImportError:
            out.append(("WARN", f"{pkg} not importable (the gym needs it)"))
    return out


def check_epoch(contract_path: str | Path, series: dict | None = None,
                ledger_paths: list[Path] | None = None) -> list[tuple[str, str]]:
    """Compute the epoch from current code/contract/data and compare to registry and ledgers."""
    from .series import compute_epoch
    out: list[tuple[str, str]] = []
    try:
        current = compute_epoch(contract_path)
    except Exception as e:
        return [("FAIL", f"cannot compute epoch: {e}")]
    out.append(("OK", f"current epoch {current}"))
    if series is not None:
        expected = series["epoch"]
        if current != expected:
            out.append(("FAIL", f"epoch mismatch: series expects {expected}, current code gives {current}"))
        else:
            out.append(("OK", f"epoch matches series registry ({expected})"))
    if ledger_paths:
        from .ledger import Ledger, LedgerTooNew
        for lp in ledger_paths:
            if not lp.exists():
                continue
            try:
                led = Ledger.open_readonly(lp)
                epochs = [r[0] for r in led.conn.execute("SELECT id FROM epochs")]
                led.close()
                for eid in epochs:
                    if eid != current:
                        out.append(("WARN", f"{lp.parent.name}: ledger epoch {eid} differs from current {current} (this ledger would start a NEW epoch)"))
            except LedgerTooNew as e:
                pass  # handled by check_ledgers
            except Exception:
                pass
    return out


def check_arm_definitions(arms: list[str], contract_path: str | Path, seed: int,
                          ledger_map: dict[str, list[Path]] | None = None) -> list[tuple[str, str]]:
    """Compare current arm_def_hash against pins and ledger starts."""
    from .experiments import arm_definition_hash
    from .pins import PINNED_HASHES
    out: list[tuple[str, str]] = []
    for arm in arms:
        try:
            h = arm_definition_hash(arm, contract_path, seed)
        except Exception as e:
            out.append(("WARN", f"{arm}: cannot compute arm_def_hash: {e}"))
            continue
        if not h:
            out.append(("WARN", f"{arm}: arm_definition_hash returned empty"))
            continue
        out.append(("OK", f"{arm}: arm_def_hash {h}"))
        # check against pins
        if arm in PINNED_HASHES:
            pinned, date, note = PINNED_HASHES[arm]
            if h != pinned:
                out.append(("FAIL", f"{arm}: pinned hash {pinned} (since {date}) != current {h}; the arm definition changed"))
            else:
                out.append(("OK", f"{arm}: matches pin ({date})"))
        # check against ledger daemon_started events
        if ledger_map and arm in ledger_map:
            from .ledger import Ledger, LedgerTooNew
            for lp in ledger_map[arm]:
                if not lp.exists():
                    continue
                try:
                    led = Ledger.open_readonly(lp)
                    starts = led.recent_events("daemon_started", 1)
                    if starts:
                        last_hash = starts[-1]["payload"].get("arm_def_hash")
                        if last_hash and last_hash != h:
                            out.append(("FAIL", f"{arm} ({lp.parent.name}): last daemon_started hash {last_hash} != current {h}; `ants run` would exit 4"))
                    led.close()
                except LedgerTooNew:
                    pass
                except Exception:
                    pass
    return out


def check_data() -> list[tuple[str, str]]:
    """Catalog readable, dev datasets present, manifest verified."""
    from .system import data_check
    level, msg = data_check()
    out = [(level, msg)]
    # manifest check
    from .home import find_catalog
    try:
        cat_path = find_catalog()
        mf = cat_path.parent / "MANIFEST.sha256"
        if mf.exists():
            from .data import Catalog
            cat = Catalog(cat_path)
            bad = cat.verify_manifest(only_existing=True)
            if bad:
                out.append(("FAIL", f"data manifest: {len(bad)} problem(s): {', '.join(bad[:3])}"))
            else:
                out.append(("OK", "data manifest verified (existing files)"))
        else:
            out.append(("WARN", f"no MANIFEST.sha256 at {mf}"))
    except Exception as e:
        out.append(("WARN", f"manifest check skipped: {e}"))
    return out


def check_disk(path: str | Path, thresholds: dict | None = None) -> list[tuple[str, str]]:
    """Free space under path.  Uses contract thresholds if given, else defaults."""
    from .janitor import free_gb
    th = thresholds or {"warn": 15, "clean": 12, "pause": 8, "hardstop": 6}
    try:
        free = free_gb(path)
    except Exception as e:
        return [("WARN", f"cannot read disk usage: {e}")]
    from .janitor import disk_level
    lvl = disk_level(free, th)                                # the very rule `ants run` uses: preflight must not say OK where the search would pause
    msg = f"disk: {free:.1f} GB free under {path} (level {lvl}; warn {th['warn']}, pause {th['pause']}, hard stop {th['hardstop']} GB)"
    if lvl in ("pause", "hardstop"):
        return [("FAIL", msg + ": the search would not run")]
    if lvl in ("warn", "clean"):
        return [("WARN", msg)]
    return [("OK", msg)]


def check_ledgers(ledger_paths: list[Path]) -> list[tuple[str, str]]:
    """Schema, integrity, chain, backup age, PAUSE, lock."""
    from .ledger import Ledger, LedgerTooNew, SCHEMA_VERSION
    from .system import backup_dir
    out: list[tuple[str, str]] = []
    for lp in ledger_paths:
        if not lp.exists():
            continue
        tag = lp.parent.name
        # schema version check before opening (open_readonly calls _refuse_newer)
        try:
            led = Ledger.open_readonly(lp)
        except LedgerTooNew as e:
            out.append(("FAIL", f"{tag}: {e}"))
            continue
        except Exception as e:
            out.append(("FAIL", f"{tag}: cannot open ledger: {e}"))
            continue
        try:
            sv = led.schema_version()
            if sv < SCHEMA_VERSION:
                out.append(("WARN", f"{tag}: schema v{sv} < current v{SCHEMA_VERSION}; will be migrated one-way on first open, a .pre-migration-v{sv} copy is made"))
            elif sv == SCHEMA_VERSION:
                out.append(("OK", f"{tag}: schema v{sv}"))

            # quick_check integrity (read-only)
            ic = [r[0] for r in led.conn.execute("PRAGMA quick_check")]
            if ic == ["ok"]:
                out.append(("OK", f"{tag}: integrity ok"))
            else:
                out.append(("FAIL", f"{tag}: integrity failed: {ic[:3]}"))

            # chain verification
            ok, bad = led.verify_chain()
            head = led.head()
            if ok:
                out.append(("OK", f"{tag}: chain intact, head {head[:16]}"))
            else:
                out.append(("FAIL", f"{tag}: chain broken at seq {bad}"))

            led.close()
        except Exception as e:
            out.append(("FAIL", f"{tag}: error during checks: {e}"))
            try:
                led.close()
            except Exception:
                pass
            continue

        # backup age
        workdir = lp.parent
        try:
            bd = backup_dir(workdir)
            if bd.exists():
                from .backup import list_backups
                bks = list_backups(bd)
                if bks:
                    age_h = (time.time() - bks[0]["created"]) / 3600
                    lvl = "OK" if age_h <= 36 else "WARN"
                    out.append((lvl, f"{tag}: newest backup {age_h:.1f} h old ({len(bks)} kept)"))
                else:
                    out.append(("WARN", f"{tag}: no backups in {bd}"))
            # else: no backup dir found; not an error for preflight
        except Exception:
            pass

        # PAUSE file
        pause_file = workdir / "PAUSE"
        if pause_file.exists():
            out.append(("WARN", f"{tag}: PAUSE file present"))

        # lock held by a live process
        lock_file = workdir / "ants.lock"
        if lock_file.exists():
            try:
                import fcntl
                fd = os.open(lock_file, os.O_RDONLY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fd, fcntl.LOCK_UN)
                    # lock was free
                except BlockingIOError:
                    pid = os.pread(fd, 32, 0).decode("ascii", "replace").strip()
                    out.append(("INFO", f"{tag}: ants.lock held by pid {pid} (a running daemon)"))
                finally:
                    os.close(fd)
            except Exception:
                pass
    return out


def check_secrets(llm_env: str | None = None) -> list[tuple[str, str]]:
    """Check that llm.env exists and has correct permissions; never print content."""
    candidates = []
    if llm_env:
        candidates.append(Path(llm_env).expanduser())
    for c in ("/etc/ants/llm.env", "~/.config/ants/llm.env"):
        candidates.append(Path(c).expanduser())
    out: list[tuple[str, str]] = []
    for p in candidates:
        if not p.exists():
            continue
        try:
            st = p.stat()
            mode = stat.S_IMODE(st.st_mode)
            if mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH):
                out.append(("WARN", f"{p}: mode {oct(mode)} is group/world readable; should be 0o600"))
            else:
                out.append(("OK", f"{p}: mode {oct(mode)}"))
        except OSError as e:
            out.append(("WARN", f"{p}: cannot stat: {e}"))
        return out  # check only the first found
    return out


def check_clock(ledger_paths: list[Path]) -> list[tuple[str, str]]:
    """Wall clock must not be earlier than the newest event in any ledger."""
    from .ledger import Ledger, LedgerTooNew
    out: list[tuple[str, str]] = []
    now = time.time()
    for lp in ledger_paths:
        if not lp.exists():
            continue
        try:
            led = Ledger.open_readonly(lp)
            row = led.conn.execute("SELECT MAX(ts) FROM events").fetchone()
            led.close()
            if row and row[0] is not None:
                newest = row[0]
                if now < newest - 300:  # 5 minutes
                    out.append(("FAIL", f"{lp.parent.name}: wall clock {now:.0f} is {newest - now:.0f}s earlier than newest event"))
        except (LedgerTooNew, Exception):
            pass
    if not out:
        out.append(("OK", "clock ok"))
    return out


def check_systemd(series_name: str | None = None) -> list[tuple[str, str]]:
    """Check systemd unit status (only when systemctl exists and --series given)."""
    if series_name is None:
        return []
    if shutil.which("systemctl") is None:
        return []
    out: list[tuple[str, str]] = []
    unit = f"ants-series@{series_name}.service"
    try:
        import subprocess
        r = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=5)
        state = r.stdout.strip()
        out.append(("INFO", f"systemd {unit}: {state}"))
    except Exception:
        pass
    return out


# ---- orchestrator -------------------------------------------------------

def gather_ledger_paths(base: Path | None, workdir: Path | None, arms: list[str] | None,
                        series: dict | None = None) -> list[Path]:
    """Find ledger.sqlite files for the given scope."""
    paths: list[Path] = []
    if workdir:
        lp = Path(workdir) / "ledger.sqlite"
        if lp.exists():
            paths.append(lp)
    if base:
        from .home import ledgers_in
        paths.extend(ledgers_in(base))
    if series and base:
        from .seeds import _workdir_name
        for arm in series.get("arms", []):
            for seed in series.get("seeds_parsed", []):
                lp = Path(base) / _workdir_name(arm, seed) / "ledger.sqlite"
                if lp.exists() and lp not in paths:
                    paths.append(lp)
    return list(dict.fromkeys(paths))  # deduplicate, preserve order


def build_ledger_map(arms: list[str], ledger_paths: list[Path]) -> dict[str, list[Path]]:
    """Map arm names to their ledger paths by directory name convention."""
    m: dict[str, list[Path]] = {a: [] for a in arms}
    for lp in ledger_paths:
        dirname = lp.parent.name
        for arm in arms:
            if dirname == arm or dirname.startswith(arm + "_s"):
                m[arm].append(lp)
    return m


def run_preflight(*, series_name: str | None = None, series_file: str | None = None,
                  workdir: str | None = None, arm: str | None = None,
                  base: str | None = None, llm_env: str | None = None,
                  contract: str | None = None,
                  as_json: bool = False, strict: bool = False) -> int:
    """Run all checks, print results, return exit code."""
    results: list[tuple[str, str]] = []

    # resolve series if given
    series = None
    arms: list[str] = []
    contract_path = None
    base_path = None
    seed = 1

    if series_name:
        from .series import load_series, SeriesError
        path = None
        if series_file:
            path = Path(series_file)
        else:
            for cand in (Path("deploy/series") / f"{series_name}.toml",
                         Path("/opt/ants/deploy/series") / f"{series_name}.toml"):
                if cand.exists():
                    path = cand
                    break
        if path is None:
            results.append(("FAIL", f"series file not found for {series_name!r}"))
        else:
            try:
                series = load_series(path)
                arms = series["arms"]
                base_path = Path(base or series["base"])
                from .cli import _contract_path
                contract_path = str(Path(_contract_path(series.get("contract", "contracts/tinyts_real.toml"))).resolve())
            except (SeriesError, Exception) as e:
                results.append(("FAIL", f"series: {e}"))
    else:
        if arm:
            arms = [a.strip() for a in arm.split(",")]
        if base:
            base_path = Path(base)
        if workdir:
            base_path = base_path or Path(workdir).parent
        if contract:
            from .cli import _contract_path
            try:
                contract_path = str(Path(_contract_path(contract)).resolve())
            except Exception:
                contract_path = contract

    # auto-find contract
    if contract_path is None:
        from .cli import CONTRACT_SEARCH
        from .home import package_root
        for cand in CONTRACT_SEARCH:
            if Path(cand).exists():
                contract_path = cand
                break
        if contract_path is None:
            root = package_root()
            if root:
                for n in ("tinyts_real.toml", "tinyts_synth_hard.toml"):
                    if (root / "contracts" / n).exists():
                        contract_path = str(root / "contracts" / n)
                        break

    # gather ledger paths
    wd_path = Path(workdir) if workdir else None
    ledger_paths = gather_ledger_paths(base_path, wd_path, arms, series)
    ledger_map = build_ledger_map(arms, ledger_paths) if arms else {}

    # a. runtime
    results.extend(check_runtime())

    # b. evaluator identity / epoch
    if contract_path:
        results.extend(check_epoch(contract_path, series, ledger_paths))

    # c. arm definitions
    if arms and contract_path:
        results.extend(check_arm_definitions(arms, contract_path, seed, ledger_map))

    # d. data
    results.extend(check_data())

    # e. disk
    disk_path = base_path or (Path(workdir) if workdir else Path("."))
    thresholds = None
    if contract_path:
        try:
            from .contract import load_contract
            c = load_contract(contract_path)
            thresholds = c.thresholds
        except Exception:
            pass
    results.extend(check_disk(disk_path, thresholds))

    # f. ledgers
    results.extend(check_ledgers(ledger_paths))

    # g. secrets
    results.extend(check_secrets(llm_env))

    # h. clock
    results.extend(check_clock(ledger_paths))

    # i. systemd
    results.extend(check_systemd(series_name))

    # output
    if as_json:
        print(json.dumps([{"level": l, "message": m} for l, m in results], indent=2))
    else:
        for level, msg in results:
            print(f"[{level:>4}] {msg}")

    has_fail = any(l == "FAIL" for l, _ in results)
    has_warn = any(l == "WARN" for l, _ in results)
    if strict:
        return 1 if (has_fail or has_warn) else 0
    return 1 if has_fail else 0
