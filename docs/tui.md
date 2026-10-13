# The terminal interface

`ants` with no arguments opens it on a terminal. It is how a person looks at a running search on the machine where it runs, without going through anyone else: SSH in, type `ants`.

```
ants          the interface (a terminal), or the line shell (a pipe, a dumb terminal, ANTS_NO_TUI=1)
ants ui       the interface, with --workdir / --contract / --arm
ants home     print one screen and exit: --screen overview|board|trials|fleet|events|health|data|system --width N --color
```

## Screens

| key | screen | shows |
|---|---|---|
| 0 | Home | the mark, one line per search (evals, deep evals, last finish, best per size cap), the directory |
| 1 | Overview | finished per 10 minutes over 6 hours, best-so-far curve, stages with bars, best per cap as gain over seasonal naive, operators |
| 2 | Leaderboard | tabs per size cap (← →), rows ranked by the deepest stage reached; Enter opens a trial |
| 3 | Trials | every evaluation, filter by state (`s`) and stage (`t`); Enter opens a trial |
| 4 | Arms | the running searches side by side, with their best-so-far curves; Enter switches to one |
| 5 | Events | the hash-chained record, filter by kind (`k`) |
| 6 | Health | `ants doctor` plus the ledger checks: integrity, schema, write-ahead log, filesystem, backup age |
| 7 | Data | datasets by role (dev, confirm, sealed, stress), which are present, size on disk |
| 8 | System | version and update, service state, model spend against its cap, agent tool reads, backups |

`/` opens a command line: `best --top 3`, `show 12`, `diff 5 7`, `log`, `status`, `sql SELECT … `, `backups`, `doctor`, `hint try a longer look-back`, `pause`, `resume`. Output goes to a pager. `top`, `export` and `workspace` are not offered here (the first never returns, the other two write files). `run`, `restore`, `service`, `janitor`, `data fetch` and `update --apply` are refused here: starting, replacing or installing things is done from a shell on purpose. `?` lists the keys, `q` quits, `r` refreshes (it refreshes every 3 seconds anyway), `a` and `w` switch arm and workdir when there are several.

## Design

1. **The interface cannot hurt the thing it watches.** Ledgers are opened with `Ledger.open_readonly`: `mode=ro`, `query_only`, no schema creation, no migration, no pragma that writes. A test compares the bytes and modification times of the ledger before and after every screen is loaded. `hint`, `pause` and `resume` are the only writes and they go through the ordinary CLI commands.
2. **Views are pure.** `views.py` turns data into styled lines and imports nothing from curses. The same lines are drawn by curses, printed by `ants home`, written as HTML in a screenshot and asserted on in tests; a screen cannot differ between those paths.
3. **The loop never waits on data.** One background thread loads what the current screen needs, every 3 seconds or when asked; the key loop draws the last result and shows its age. A slow or broken ledger produces a line that says why on that screen, not a frozen terminal.
4. **It degrades.** Without curses, a tty, or a usable `TERM`, plain `ants` falls back to the line shell; `NO_COLOR` is honoured; lines are cut to the terminal width (never wrapped) and the pixel word is dropped below 80 columns.
5. **A view bug is a line, not a crash.** Exceptions inside a view and inside loading are caught and shown; the terminal is restored by `curses.wrapper` in every case.
6. **One source of numbers.** Every figure comes from the functions the CLI already uses (`ui.snapshot`, `leaderboard`, `show`, `log`, `doctor`, `durability_checks`); the interface adds layout, not logic.

Pixel art: the mark is built from shapes on a 30×16 pixel mint tile and drawn two pixels per cell with half blocks (`▀`, foreground the upper pixel, background the lower); the word is a 4×5 alphabet, two cells per pixel. Bars are eighths of a cell, sparklines the eight block heights. Colours are the figure palette (mint, ink); in 256-colour terminals they are the nearest cube colours.

Not here, on purpose: a mouse, a web interface (`ants serve` is the localhost board), and anything that changes the search beyond a hint or a pause.
