"""Unit files that keep `ants run` resident, rendered as plain text (systemd) or a property list (launchd). Standard library only.

Rendering is pure so that it can be tested and printed (`ants service install --print`); installing only writes the files and asks
the service manager to load them. The systemd unit uses Type=notify with a watchdog: the loop sends its own heartbeat (see notify.py),
so a hung loop is restarted and a long evaluation is not. Exit status 3 (another daemon holds the workdir) is never restarted.
"""
from __future__ import annotations

import plistlib
import shlex
import sys
from pathlib import Path

WATCHDOG_S = 300                    # a blocked LLM call is abandoned after 100 s; this leaves three of them of slack
THREADS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")


def command(extra: list[str], python: str | None = None) -> list[str]:
    """`python -m antslab.cli ...`: the interpreter that is running now, so a venv or the npm launcher's private venv keeps working"""
    return [python or sys.executable, "-m", "antslab.cli", *extra]


def unit_name(name: str) -> str:
    return f"ants-{name}"


def run_args(contract: str, workdir: str, arm: str, seed: int = 1, llm_env: str | None = None) -> list[str]:
    a = ["run", "--contract", contract, "--workdir", workdir, "--arm", arm, "--seed", str(seed)]
    if llm_env:
        a += ["--llm-env", llm_env]
    return a


def _env_lines(env: dict[str, str]) -> list[str]:
    q = lambda x: x.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")      # systemd: double quotes, `%` is a specifier
    return [f'Environment="{q(k)}={q(v)}"' for k, v in sorted(env.items())]


def render_systemd(name: str, argv: list[str], env: dict[str, str], user: bool = True, watchdog_s: int = WATCHDOG_S) -> str:
    env = {**{k: "1" for k in THREADS}, **env}
    lines = [
        "[Unit]",
        f"Description=ants ({name})",
        "Wants=network-online.target",
        "After=network-online.target",
        "# never give up: RestartSec spaces the retries, and `ants doctor` makes a crash loop visible",
        "StartLimitIntervalSec=0",
        "",
        "[Service]",
        "Type=notify",
        "NotifyAccess=main",
        f"WatchdogSec={watchdog_s}",
        "ExecStart=" + " ".join(shlex.quote(x) for x in argv),
        "Restart=always",
        "RestartSec=30",
        "# exit 3 = another ants holds this workdir: do not restart-loop",
        "RestartPreventExitStatus=3",
        "TimeoutStopSec=60",
        "KillMode=control-group",
        "# under memory pressure the evaluation goes first, never sshd",
        "OOMScoreAdjust=500",
        *_env_lines(env),
        "",
        "[Install]",
        f"WantedBy={'default.target' if user else 'multi-user.target'}",
        "",
    ]
    return "\n".join(lines)


def render_backup_units(name: str, argv: list[str], env: dict[str, str], user: bool = True) -> tuple[str, str]:
    svc = "\n".join([
        "[Unit]", f"Description=ants backup ({name})", "",
        "[Service]", "Type=oneshot", "ExecStart=" + " ".join(shlex.quote(x) for x in argv), *_env_lines(env),
        "Nice=10", "IOSchedulingClass=idle", "",
    ])
    timer = "\n".join([
        "[Unit]", f"Description=daily ants backup ({name})", "",
        "[Timer]", "OnCalendar=*-*-* 03:15:00", "RandomizedDelaySec=15m", "Persistent=true", "",
        "[Install]", f"WantedBy={'timers.target'}", "",
    ])
    return svc, timer


def render_plist(name: str, argv: list[str], env: dict[str, str], logdir: Path) -> bytes:
    """launchd: restart after any failure, 30 s apart; stdout and stderr go to <logdir>/ants-<name>.log"""
    env = {**{k: "1" for k in THREADS}, **env}
    d = {
        "Label": f"io.ptoq.{unit_name(name)}",
        "ProgramArguments": argv,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "ProcessType": "Background",
        "EnvironmentVariables": env,
        "StandardOutPath": str(logdir / f"{unit_name(name)}.log"),
        "StandardErrorPath": str(logdir / f"{unit_name(name)}.log"),
    }
    return plistlib.dumps(d)


def systemd_dir(user: bool = True) -> Path:
    return Path.home() / ".config" / "systemd" / "user" if user else Path("/etc/systemd/system")


def launchd_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"
