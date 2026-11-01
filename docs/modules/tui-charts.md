# tui.charts

Small pure terminal plots used by Health, Data and System. Standard library only; no ANSI output, global terminal state or refresh loop. Existing styled-line rendering supplies colour, sizing and sanitization around the plots.

`scatter(series, width, height, xlim, ylim, xlabel, ylabel, ascii_only)` receives labelled `(x,y)` observations. It prints numeric X/Y ticks and units, ignores nonfinite/missing observations, leaves gaps unconnected and marks coincident cells `+`. No smoothing, fabricated intermediate observations or inferred uncertainty. Empty input says no observations. Too-narrow terminals get guidance rather than a clipped axis. Explicit limits preserve comparison scales. `role_bars` shows declared catalog counts and observed file presence; generated entries are separate and presence is not hash verification.

Health's `v` chart mode requests a current-arm/current-epoch query from `Source.health_extra(trends=True)`: last six hours, twelve half-hour bins. Failed includes failed/timeout/crashed terminal outcomes; denominator includes those and done. Empty denominators do not produce a 0% rate. Throughput can be zero in a known empty bin. The failure-rate axis starts at zero and scales to the observed range with a 10% minimum upper bound; ticks and the scaled-axis label make this explicit. Display the observation sample time in UTC and do not claim persisted historical host monitoring. Normal checks remain available with `v`.

Data's `v` chart mode shows declared role counts plus a family/group count matrix from metadata. It does not call `Catalog.load`, parse raw values, verify hashes or open protected series. Missing or inaccessible paths are unavailable, not necessarily deleted. Generated and declared data roles remain distinct in the chart; role policy is unchanged.

System's `v` chart mode plots recorded archive sizes in decimal MB against days before the sample, restricted to the last seven days. Unknown sizes are omitted, not zero; the chart does not re-verify backup integrity. Existing details, spending and backup inventory remain accessible with `v`. No fabricated CPU/memory history is shown.

References reviewed November 2, 2026: [plotext](https://github.com/piccolomo/plotext) and its [date axes](https://plotext.readthedocs.io/en/latest/date.html), [plotille](https://github.com/tammoippen/plotille), and [Rich Live](https://rich.readthedocs.io/en/latest/live.html). These informed the chart/refresh design; no code was copied and no runtime dependency was added.

Tests: `tests/test_terminal_charts.py`. Status: experimental.

## 0.6.4 default dashboards

Health, Data and System now show essential data and plots together. `v` opens full details; it is not required to discover the charts. Layout is bounded for 80×24 and 100×30, with explicit detail hints when entries are condensed. Health queries scoped outcome bins in the default dashboard. System follows the series timer layout `backups/series/<series>/<workdir>` and the owning series service, with explicit env/config backup overrides taking priority. Backup plots show the actual available receipt span in minutes/hours/days, rather than assuming seven days. Missing directories, empty histories, unreadable paths and invalid sidecars have distinct wrapped messages. Incomplete time/size/file metadata is omitted and counted. Viewing does not verify the archive bytes. Earlier chart-mode descriptions above document 0.6.3.

## Research activity heatmap

The System dashboard gives current-cohort operational activity a date × local-hour grid, distinct from backup history. `c` switches recorded evaluation CPU minutes and successfully completed trial counts. Read-only SQL is scoped by each source gym/arm/epoch, with resolved-source deduplication. CPU includes recorded terminal outcomes and is attributed to finish hour; it is not instantaneous host utilisation or a quality/research-contribution score. Invalid/missing CPU values and unreadable sources are counted, not silently treated as zero. Before the earliest recorded creation and after the sample time, cells are outside coverage; a covered zero means no logged quantity, including when an evaluation is still running.

The bounded grid contains up to seven dates and 24 hours, condensed according to terminal height; displayed totals and intensity maxima use only visible dates. Local timezone/offset, peak per hour and total are shown. Backup receipts remain separate, with the full archive-size plot available in the dedicated chart/detail view; no backup is inferred from activity. No extra monitoring daemon, raw data loading or historical-ledger scan is introduced.
