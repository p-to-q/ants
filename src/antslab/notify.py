"""systemd readiness and watchdog notifications (sd_notify) without any dependency.

Under `Type=notify` + `WatchdogSec=`, systemd restarts a service that stops sending WATCHDOG=1. The heartbeat is sent from the loop
itself (not from a helper thread), so a loop that hangs stops beating and is restarted, while a long evaluation, which keeps polling
its stop flag every quarter second, keeps beating. Outside systemd every call is a no-op.
"""
from __future__ import annotations

import os
import socket
import time

_last = 0.0


def sd_notify(message: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):                       # abstract socket
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(message.encode())
        return True
    except OSError:
        return False


def watchdog_interval() -> float | None:
    """seconds between heartbeats (half the configured watchdog), or None when no watchdog is set"""
    try:
        usec = int(os.environ["WATCHDOG_USEC"])
    except (KeyError, ValueError):
        return None
    return max(1.0, usec / 2e6) if usec > 0 else None


def heartbeat(now: float | None = None) -> bool:
    """send WATCHDOG=1 if the interval has passed since the last one"""
    global _last
    every = watchdog_interval()
    if every is None:
        return False
    t = time.monotonic() if now is None else now
    if t - _last < every:
        return False
    _last = t
    return sd_notify("WATCHDOG=1")
