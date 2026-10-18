# The terminal interface (manual)

`ants` with no arguments opens it on a terminal. It is how a person looks at running searches on the machine where they run, without asking anyone else to run commands: SSH in, type `ants`.

```
ants          the interface (a terminal), or the line shell (a pipe, a dumb terminal, ANTS_NO_TUI=1)
ants ui       the interface, with --workdir / --contract / --arm
ants home     print one screen and exit: --screen home|overview|board|trials|fleet|events|health|data|system|afterlife --width N --color
```

## What you see first

The home screen has the ant (its cursor pixels beat once every 1.6 s while the interface is alive), the name and one line of what ants is, a two-line introduction, one block per running search, and the directory.

Each search shows an animated glyph, a short plain-language phrase and the reason behind it, then the numbers:

```
 ⣾ portfolio       2469   239  101   42m    0.949·s0  0.965·s2  0.803·s2  0.794·s2  ▂▃▅▇▆▅▆█
     Going well  101 evaluations this hour, last new best 38 min ago
```

| phrase | when | glyph |
|---|---|---|
| Warming up | no evaluation has finished yet | loading |
| Going well | at least 70% of the usual rate, and a new best within the hour | running |
| Just found something | a new best in the last 10 minutes | burst |
| Steady | the rate is normal, the last new best is 1 to 6 hours old | running |
| Exploring | the rate is normal, no new best for over 6 hours | running |
| A bit slow / A bit quiet | the hour's rate is below 70% / 30% of the median of the last six hours | running |
| Waiting on the model | more than half of the model calls of the last hour failed; the search carries on without | waiting |
| Stuck? | nothing has finished for 30 minutes | still dot |
| Needs a look | over a quarter of the last 40 evaluations failed, the hash chain is broken, or the last backup is over 48 h old | – |
| Running low on space | under 5 GB free | running |
| Paused | the PAUSE flag is set; `p` resumes | still dot |

The phrase always sits next to the numbers it came from and always says why. It cheers only for real events and never jokes about a failure. The rules are in `src/antslab/tui/vibe.py`, one test per row.

## Screens

The final screen is **Afterlife** (`9`). It groups past work by research round,
not trial. Its subtitle explains the page in complete prose. Arrow and Page keys
select a round; Enter shows its evidence and parameters; Esc returns to the same
selection. A cached receipt is not a live backup check. Operational snapshots
omit results by default. See [the history/cache reference](modules/archive_index.md)
for indexing, empty states, failures and scientific interpretation.

Navigation, numeric shortcuts and the header share one screen registry. Running
series are discovered one level below `/var/lib/ants/series` (override with
`ANTS_SERIES_DIR`); archived workdirs are not recursively revived as live searches.

| key | screen | what is drawn |
|---|---|---|
| 0 | Home | the ant, the one line and intro, each search with its phrase and a 6-hour activity strip, the directory |
| 1 | Overview | the phrase and reason; what is being worked on now (a spinner per running trial); a braille chart of the best value so far; finished per 10 minutes as a heat strip and a sparkline; each stage as a stacked bar (done, running, failed); best per size cap as gain bars; each searcher's promotion rate; model calls per hour |
| 2 | Leaderboard | tabs per size cap (`Tab` / `Shift-Tab`, `<` `>`, `c`; the tab ≤N lists every model of at most N parameters, so a small model also appears under the larger caps); rows ranked by the deepest stage reached, with gain bars, a log-scale lollipop of the parameter count, ▲ ▼ for rank changes since the last refresh; Enter opens a trial |
| 3 | Trials | a ribbon of the newest trials coloured by state, then one row each with a state glyph and a duration bar; filter by state (`s`) and stage (`t`); Enter opens a trial |
| 4 | Arms | the searches side by side with their phrases; evaluation counts with the equal-budget cut marked `│`; best-so-far curves on one scale; a matrix of who proposed what; the table at equal budget |
| 5 | Events | the hash-chained record with a coloured gutter per kind and events per minute over the last hour; filter by kind (`k`) |
| 6 | Health | one tile per check (■ ok, ▲ warning, ✗ failing), the check lines, gauges for backup age, disk use and ledger size |
| 7 | Data | datasets by role as tiles (■ present, □ missing), size on disk |
| 8 | System | version and update, service state, model calls of this search and of every ledger with the cap (the cap applies to each ledger on its own) and where it comes from, calls per hour, backups on a 7-day axis and how many trials a restore of the newest one would lose |

A trial opens to its levers, its metrics per stage with a bar, its error if any, and its lineage.

## Keys

| key | does |
|---|---|
| `1`–`8`, `0` | go to a screen (0 = home); `←` `→` always step through screens, on every screen (on the Leaderboard the cap tabs have their own keys: `Tab` / `Shift-Tab`, `<` `>`, `c`) |
| `↑` `↓` `PgUp` `PgDn` `g` `G` | move, scroll, top, bottom |
| `Enter` | open the selected trial, or switch to the selected search |
| `Esc` `b` | back |
| `p` | pause or resume the search on screen (sets or removes its PAUSE flag; the loop finishes its step and idles) |
| `h` | write a hint for the searchers (a suggestion, never a rule; hints do not change gates or budgets) |
| `[` `]` | previous or next search |
| `space` | freeze or unfreeze the refresh (the age tag turns yellow while frozen) |
| `r` | refresh now (it refreshes by itself every 3 s) |
| `a` `w` | next arm, next workdir |
| `/` | command line: `best --top 3`, `show 12`, `diff 5 7`, `log`, `status`, `sql SELECT …`, `backups`, `doctor`, `hint …`, `pause`, `resume`; output goes to a pager |
| `?` | keys and the words used |
| `q` | quit |

`run`, `restore`, `service`, `janitor`, `data fetch`, `update --apply`, `top`, `export` and `workspace` are refused on the command line: starting, replacing or installing things is done from a shell on purpose, `top` never returns and the last two write files.

## Animation, and turning it down

The key loop is the clock: about 7 frames a second, 2.5 over SSH. Frames are computed from the time, never counted, so a slow redraw cannot make an animation lag, and curses sends only the cells that changed. One spinner per activity: loading `⠋⠙⠹…`, a search running `⣾⣽⣻…`, waiting for a model `∙●∙`, a new best `✶✸✹…`, alive `-\|/` in the status bar.

| to get | set |
|---|---|
| no motion (one still glyph per activity, the ant's cursor stays lit) | `ANTS_REDUCE_MOTION=1` |
| no colour | `NO_COLOR=1` |
| the line shell instead of the interface | `ANTS_NO_TUI=1` |
| ASCII spinners | a locale that is not UTF-8 |

## Troubleshooting

| symptom | cause | do |
|---|---|---|
| `ants` prints that it is not set up | no config and no ledger anywhere ants looks (`/var/lib/ants`, `~/.ants/runs`, `./runs`) | `ants onboard`, or `ants ui --workdir DIR` |
| a search shows an error row | its ledger cannot be opened (corrupt, from a newer ants, unreadable) | the row names the reason; open Health on a search that works; `ants doctor --workdir DIR` |
| the screen says `loading…` for long | a writer holds the ledger exclusively (a rollback-journal file) | wait; ledgers in WAL mode, which ants uses, never block readers |
| boxes or `?` instead of block glyphs | the terminal font or locale | `LANG=C.UTF-8`, a font with block and braille glyphs; or `ANTS_NO_TUI=1` |
| keys do nothing inside tmux or screen | the multiplexer swallows them | `TERM=screen-256color`; `Esc` delays are a tmux option (`escape-time`) |
| `p` says it cannot pause | the viewer cannot write the workdir | run as the service user (`/usr/local/bin/ants` on the server does) |

## Design

1. **The interface cannot hurt the thing it watches.** Ledgers are opened with `Ledger.open_readonly`: `mode=ro`, `query_only`, no schema creation, no migration; where the directory is read-only and there is no write-ahead log it falls back to an immutable read. A test compares bytes and modification times of the ledger before and after every screen is loaded. `p`, `h` and the three commands above are the only writes and they go through the ordinary CLI.
2. **Views are pure.** `views.py` turns data and a clock into styled lines and imports nothing from curses. The same lines are drawn by curses, printed by `ants home`, written as HTML for a screenshot and asserted on in tests.
3. **The loop never waits on data.** One background thread loads what the current screen needs; the key loop draws the last result and shows its age. Data loaded for another screen is never handed to a view. A broken ledger is a row that says why.
4. **It degrades.** No curses, no tty or a dumb `TERM`: the line shell. `NO_COLOR`, reduced motion and a non-UTF-8 locale are honoured; lines are cut to the width, never wrapped; the pixel word is dropped below 84 columns.
5. **A bug is a line, not a crash.** Exceptions in a view or while loading are caught and shown; `curses.wrapper` restores the terminal in every case. Strings that a gym wrote are cleaned of control characters before they reach the screen.
6. **One source of numbers.** Everything comes from the functions the CLI already uses (`ui.snapshot`, `leaderboard`, `show`, `log`, `doctor`, `durability_checks`); the interface adds layout and the plain-language rules, not logic.

Drawing: the ant is a 23 × 12 pixel grid (`antslab.tui.pixel.ANT_GRID`) drawn two pixels per cell with half blocks (`▀`, foreground the upper pixel, background the lower); the word is a 4 × 5 alphabet, two cells per pixel. Bars use eighths of a cell, sparklines the eight block heights, charts braille (2 × 4 dots per cell) and the heat strip `·░▒▓█`. Colours are the figure palette (mint, ink); in 256-colour terminals the nearest cube colours. The logo files under `assets/` are unchanged; the terminal has its own ant.

To add a screen: a loader branch in `App.load`, a view function in `views.py` returning `View(lines, rows, row0)`, an entry in `SCREENS`, `KEYS` and the key map, and a case in `tests/test_tui.py`.

Not here, on purpose: a mouse, a web interface (`ants serve` is the localhost board), and anything that changes a search beyond a hint or a pause.
