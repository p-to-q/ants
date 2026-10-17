# Security model of ants

**Report a vulnerability:** write to hi@ptoq.io (private; please do not open a public issue). Include the version, steps to reproduce and the impact. Expect an answer within a week.

## What is trusted, what is not
* **Trusted:** the code in this repository as installed by the operator, the operator's contract (`contracts/*.toml`), the operator's own commands (`ants hint`, `pause`), the host OS and the `ants` service user.
* **Untrusted data (never code, never instructions):** every LLM reply (proposals, rationale, lessons, repair patches), every HTTP response (LLM endpoints, Jev), free text in the ledger that came from outside (hints, gym stderr, error strings), gym `metrics.json`, and any contract or package file received from someone else.
* **Never trusted to decide:** an LLM or a judge. They propose; deterministic code (strict `Space.validate`, the evidence gate, the patch gate) decides.

## What each layer defends
| layer | defends against | where |
|---|---|---|
| strict search space | an LLM smuggling anything but typed, in-range values into a trial | `contract.Space.validate` (type-exact, never raises anything but `SpecError`; fuzzed 20k cases) |
| strict JSON | NaN/Infinity, duplicate keys, deep nesting, giant or slow bodies, quadratic scans | `textsafe`, `llm.extract_json`, `llm.http_post_json` (4 MB cap, deadline, no redirects) |
| contract loader | hostile or malformed contracts (format-string commands, nan bounds, unsorted caps, secret-forwarding env, importing arbitrary modules) | `contract.load_contract` fails closed with `SpecError` |
| patch gate (maintainer) | an LLM-drafted patch touching protected code or adding dangerous constructs | path checks (no symlinks, exact case, no traversal), size limits, AST before/after comparison, tests, golden replay, rate limit, byte-exact rollback |
| transport | key leaks | https only (http only to loopback), redirects never followed, key redacted before truncation, clean child environments |
| output | terminal/HTML injection from stored text | `textsafe.clean_text` in `ui`, escaping + CSP in `board`, loopback-only server with Host check |
| packaging | secrets in a release | `scripts/pack_public.sh` scans staging AND the final tarball (`scan_secrets.py`), refuses otherwise |
| deployment | a compromised service | systemd sandbox (see `deploy/ants@.service`), no secrets in the environment, key file mode 600 owned by `ants` |

## Known limits (be honest)
* **There is no operating-system isolation between the searchers, the evaluation processes and the ledger.** Evaluation children run under the same OS user as the daemon, with a clean environment, rlimits, a process group and a timeout, and the daemon as a whole is sandboxed by its systemd unit; nothing separates an evaluation process from the ledger, the key file's directory or the data on disk. What exists is a strict **application-level** boundary (dataset roles enforced by environment and code, a closed tool set for the agent operator, strict output parsing, fencing tokens, the hash chain), not a sandbox that has been shown to hold. Today that is acceptable because the code that runs in evaluation is the repository's own gym and the proposers return specs, never code. It stops being acceptable the moment any proposer writes code: that needs the isolation in [roadmap](docs/roadmap.md) step 25 first.
* The AST gate is best-effort static analysis, **not a sandbox**. Patched code runs, in a throw-away copy, with the privileges of the operator's user (clean environment, rlimits, timeout, own process group). Keep the maintainer disabled on machines that hold anything valuable beyond the lab.
* Prompt injection cannot be fully prevented; its impact is limited by design (a rationale is only logged; hints and lessons cannot change a gate, a budget or a cap).
* `MemoryDenyWriteExecute` and `SystemCallFilter` are deliberately not set (numpy/onnxruntime; untestable without the target host). `/opt/ants` is owned by the service user; the unit makes it read-only at run time.
* The board is read-only and bound to 127.0.0.1; reach it through an SSH tunnel. Do not expose it.
