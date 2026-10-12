# Preflight (do these yourself before a 24/7 run; the installer deliberately does not)

1. **SSH:** keys only (`PasswordAuthentication no`, `PermitRootLogin no`), after confirming key login works from a second session.
2. **Firewall:** allow 22 only from your addresses if possible; do not expose 8765 (board) or any gateway port. The board is localhost-only by design.
3. **Updates:** `apt-get update && apt-get upgrade`, reboot if the kernel changed.
4. **Swap:** 2 GB swap file as a *buffer* (`fallocate -l 2G /swapfile; chmod 600 /swapfile; mkswap /swapfile; swapon /swapfile`); the service limits keep the training slice from using it.
5. **Disk levels** (contract `[disk]`): warn 15 GB, clean 12, pause 8, hardstop 6.
6. **Secrets:** `/etc/ants/llm.env` mode **600, owner ants** (`install -m 600 -o ants -g ants /dev/stdin /etc/ants/llm.env`): the client refuses any file with group/other bits, and 640 root:ants would stop every LLM arm at start. The units do not load it into the environment. Never inside `/opt/ants` or any archive.
7. **Sandbox check:** after `systemctl enable --now`, run `systemd-analyze security ants@portfolio` (exposure should be well below the stock 9.6 UNSAFE) and `journalctl -u ants@portfolio -b | head`; if a hardening directive turns out to break something on this host, remove only that line and note it in SECURITY.md.
8. **Monthly check (5 minutes):** `ants status` (chain intact?), free disk, last archive time, `journalctl -u ants@portfolio --since "30 days ago" -p warning`, board.
