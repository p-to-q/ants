# board

Leaderboard JSON includes standardised `val_mse` and `rel_mae` where present;
HTML shows them alongside the actual named primary metric. Missing aggregates
are `-`, not invented values, and the primary ranking rule is unchanged.

Projection surface: `board.json` + self-contained `board.html` (meta-refresh every 60 s, no external requests, no scripts). Read-only; never a dependency of the loop.

**Interface**
- `build(ledger, contract, arms, out_dir, top=5)`, `render(data)`; CLI `board` (one-off) and `serve` (HTTP on 127.0.0.1, regenerates every `--every` s). `make_server(directory, port, host="127.0.0.1")` returns the server; `BoardHandler` is its handler.
- Content: per-cap ranking with lineage depth, category belts, path kinematics table, operator discovery rates, ruggedness, stepping-stone statistics, judge prediction counts, chain status and head.

**Invariants**
- **Every** interpolated value is HTML-escaped whatever its type, and the page carries a Content-Security-Policy meta tag (`default-src 'none'`) that forbids scripts and any request. `board.json` and `board.html` are replaced atomically (temp file + rename), so a reader never sees half a page.
- `make_server` / `BoardHandler`: refuses to bind anything but a loopback host (in practice `127.0.0.1`: the `::1` spelling passes the check but the IPv4 socket cannot bind it); serves exactly `/`, `/board.html` and `/board.json` from one directory; GET/HEAD only (405 otherwise); refuses symlinks and non-regular or over-32 MiB files; a `Host` header that is not a loopback name (or has a different port) gets 403 (DNS-rebinding defence); sends CSP, `nosniff`, `no-referrer`, `no-store`, `same-origin` and `X-Frame-Options` headers; does not log request lines. `ants serve` uses it.

**Notes / failure behaviour**
- Optional: the loop never depends on it; the refresh thread of `ants serve` swallows and prints errors.
- `build` reads `History` without an epoch filter: after an evaluator or data change the board mixes epochs (the chain status shown is the full `verify_chain`).
- There is no authentication; reach it through an SSH tunnel. The `ants-board.service` unit has `IPAddressDeny=any` except localhost.
