# Scaling

ants runs on a 2 vCPU / 2 GB machine today. This page says what grows by configuration, what needs code, and what is not designed yet.

## What grows by configuration

| to grow | change | notes |
|---|---|---|
| model size | `[objective] caps` in the contract | a new contract is a new epoch; old results stay queryable and are never mixed |
| memory per evaluation | `mem_cap_mb`, `ANTS_MAX_WINDOW_ELEMS` | the pre-filter reads `est_mem_mb`; refit it on the new machine |
| time per evaluation | `[budget] stage_timeouts` | per stage, in seconds |
| more arms at once | one workdir and one `ants@<arm>.service` per arm | each arm has its own ledger and lock; `CPUQuota` and `MemoryMax` split the machine |
| more data | `data/catalog.toml` | roles are fixed there; a new dataset is a new epoch |
| more language-model throughput | more endpoints in the env file | the proposer keeps a bounded number of calls in flight |

## What needs code

| to grow | gap | status |
|---|---|---|
| several workers on one arm | `Campaign.step` runs one evaluation at a time and a workdir lock allows one `ants run`; claims in the ledger are already atomic, so a worker pool is a loop change, not a data change | not implemented |
| GPU or neural gyms | a gym is an external command that writes `metrics.json`; the loop does not care what runs inside | the `nn` extra is a placeholder |
| a different task domain | a gym plus a contract ([gyms](gyms.md), [contracts](contracts.md)) | works today |
| a ledger on more than one machine | SQLite with WAL is one file on one host | not designed |
| long runs | the ledger grows about 2.4 KB per trial; History keeps a bounded window in memory | export and retention policy: see [operations](operations.md) |

## What does not change with scale

- The evaluator, the contract and the data roles stay out of reach of proposers. More compute never relaxes that.
- Each dataset is looked at under its role: many looks for development data, few for confirmation, one for sealed data.
- A bigger machine is not a new epoch; a changed evaluator, contract or catalog is.
- Arms are compared at equal evaluation budgets, whatever the hardware.

## Measured, for sizing

About 5 × 10⁴ rows a day per worker on the synthetic profile, on a laptop; the real-data profile is slower and the 2 GB server has not been measured ([operations](operations.md)). An evaluation peaks at roughly 540 to 800 MB in the real-data profile at the moment, which is why the memory cap exists.
