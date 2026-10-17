# Versions, upgrades and keeping experiments alive

The rule: **a new version of the code never costs an experiment.** What a run has produced lives in `<workdir>/` (the ledger, the per-trial files, backups and the archive); code, units and contracts are replaced around it. This page says what each kind of change does to a running search, what is checked, and how to go back.

## What is where

| thing | where | replaced by an upgrade? | protected by |
|---|---|---|---|
| ledger (every trial, event, chain) | `<workdir>/ledger.sqlite` | never | schema version in the file, forward-only migrations, a full copy before the first migration of data (`ledger.sqlite.pre-migration-v<N>`), refusal to open a ledger from a newer ants |
| per-trial files, logs | `<workdir>/runs/` | never | the janitor's delete conditions |
| backups and anchors | `/var/lib/ants/backups/<arm>/` (`~/.ants/backups` locally) | never; the deploy adds one first | sha256 sidecar, integrity and chain check |
| old runs | `/var/lib/ants/archive/old-runs/` | never | moved there by hand, never deleted |
| code | `/opt/ants` (npm: `~/.ants/venv-<version>`) | yes | the package hash is checked on the server before it runs |
| contract and data catalog | `/opt/ants/contracts`, `data/` | the files yes; raw and processed data no | the contract is hashed into the epoch; raw files against the pinned manifest |
| units and timers | `/etc/systemd/system/ants*` | yes | `systemd-analyze verify`, tests on the text |

## What a change does to a running search

| change | example | effect on the ledger | what to do |
|---|---|---|---|
| code outside the measuring machinery | interface, CLI, deploy scripts, documentation | none | upgrade; restart is optional |
| code that the loop runs | proposers, campaign, ledger | none on past data; the arm may behave differently from now on | restart the arm; if its definition changed (operators, shares, budgets) the ledger records it and `ants doctor` warns, see below |
| the measuring machinery | evaluator, metrics, data, **the contract** | a new epoch inside the same ledger: new trials carry the new epoch id, old ones keep theirs, and `best`, `top` and the interface read the current epoch only | nothing is lost; compare nothing across epochs; `ants epochs` lists them |
| a ledger schema step | a new column (e.g. v2: `call_id`, `attempt`, `arm` on `llm_calls`) | migrated in place once, after a full copy (`ledger.sqlite.pre-migration-v1`) | upgrade the code first, then start; downgrading the code needs the copy (below). The pre-migration copy is kept and should not be deleted until the new version has run long enough to trust |
| an arm's definition | the share of the model operators, a new operator | none, but past and future results are different experiments | start a **fresh workdir** (a new arm name) when the aim is a comparison; keep the old one |

Two records make this checkable: every start of the loop writes a `daemon_started` event (ants version, epoch, python, the arm definition and its hash), and `ants epochs` shows per epoch the trial counts, the dates and the versions that ran in it. `ants doctor` warns when the arm definition changed inside a ledger.

## Upgrading, step by step

On a server (`deploy/remote_deploy.sh`):

1. `ants backup` of every ledger is run by the install step itself; the deploy stops if one fails. A line with the date, the version and the package hash is appended to `/var/lib/ants/DEPLOY.log`.
2. The code tree is replaced (`rsync --delete`, excluding data, runs, ledgers, env files, the installed contract and `data/raw`), the venv is updated from the hashed lock, units are reinstalled and reloaded. Nothing under `/var/lib/ants` is touched.
3. Running arms keep the old code in memory until restarted; restart one at a time (`systemctl restart ants@<arm>`). SIGTERM requeues the trial in flight; it is evaluated again, never lost or counted twice.
4. Check `ants` (or `ants doctor --arm <arm> --workdir /var/lib/ants/<arm>`): chain intact, one new `daemon_started`, the epoch you expect.

Locally or from npm: `ants update` looks for a newer version (cached for a day), `ants update --apply` installs it with the method that installed ants, `ants service restart` loads it. The npm launcher keeps one environment per version, so the old one stays until you delete it.

## Compatibility

| interface | promise within 0.x |
|---|---|
| ledger file | an older ants never opens a newer ledger (it refuses, untouched); a newer ants opens every older ledger and migrates it after a copy |
| `--json` outputs | fields are added, not renamed or removed, within a minor version |
| contract format | unknown keys are rejected; a key's meaning does not change inside a minor version; a new key with a default that changes behaviour is announced in `docs/changes.md` and is a new epoch |
| config (`config.toml`), `llm.env`, units | additions only; an unreadable config is reported, never silently replaced |
| exit codes | 2 failing check, 3 workdir locked, 1 not found; fixed |
| version numbers | patch (0.5.1): fixes, never a new epoch or arm definition. Minor (0.6.0): may add commands and screens, change contract defaults and arm definitions, always with a `changes.md` entry that says whether it starts a new epoch |

## Going back

| situation | do |
|---|---|
| the new code misbehaves, the ledger was not migrated | install the old package (`npm i -g ants-lab@<old>`, or redeploy the old tarball), restart; the ledger needs nothing |
| the new code migrated the ledger and the old code refuses it (`LedgerTooNew`) | stop the arm; `ants restore` the backup taken by the deploy (or copy `ledger.sqlite.pre-migration-v<N>` back); results since the migration are in the newer ledger, which is kept as `ledger.sqlite.replaced-*` |
| a restore would forget events | `ants restore` refuses unless `--allow-different`; it also writes a `restored` event |
| an experiment was started on the wrong arm definition | the workdir stays as it is; start a new one and compare there |

## Rules for changing the harness without losing results

1. Never edit an old migration; append one and bump `SCHEMA_VERSION`.
2. Never reuse an arm name for a different definition; add a suffix (`portfolio_llm2`) and keep the old workdir.
3. Change the contract, evaluator or data only knowingly: it starts a new epoch, and the roadmap says why.
4. Never delete a workdir to fix a problem; move it to `archive/old-runs/`.
5. Deploy only from a tagged, CI-green commit; the install records the package hash.
