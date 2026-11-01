# Deployment gate (Ubuntu installer)

The stable `/usr/local/bin/ants` launcher consults `/usr/local/lib/ants/deploy_gate.py` with system Python before entering the managed environment. State lives outside the replacement tree in `/var/lib/ants-deploy/state.json`; non-secret phase history is appended to `history.jsonl`. `ants deploy-status [--json]` reads this state without importing ants. Legacy installations with no record are explicitly unrecorded, not verified ready.

Installer phases are preparing, installing, verifying, ready or failed. Publication uses an atomic state replacement and fsync. Only the explicit successful readiness path opens the gate. A failed exit records failure; SIGKILL leaves a blocked phase. There is no automatic age-based clearing. Readers reject invalid/oversized/symlink records and return exit 75 for unready installations rather than offering onboarding. Retry is an explicit new installation attempt; the gate does not promise an automatic atomic venv rollback.

An exclusive `/run/lock/ants-deploy.lock` serializes installers. The installer refuses while scientific `ants@*` or `ants-series@*` units are active, activating, reloading or stopping. Stop them gracefully and verify/pull backups before installation. Manually launched unmanaged processes are outside this systemd check and must also be stopped by the operator.

Readiness imports the installed modules, verifies package version, rediscovers an existing setup and checks that an existing evaluator epoch is unchanged. A fresh installation can still onboard after ready. This gate covers the managed server wrapper; a direct `/opt/ants/.venv/bin/ants` invocation bypasses it and is reserved for installer diagnostics.

Tests: `tests/test_deploy_gate.py`, plus target-server active-service refusal and actual install/readiness smoke. The gate is not a database or scientific evidence migration.
