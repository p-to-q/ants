"""Pinned arm-definition hashes: an arm that already has ledgers may not change under the same name.

Each entry records the arm_def_hash the CURRENT code gives for that arm name. If the code
changes the definition, the test (test_preflight.py::test_pinned_hashes_match_code) fails: the
fix is to create a NEW arm name (e.g. portfolio_v2) for the new definition, not to update the pin.
See docs/upgrading.md for the full rule.

To add a pin:
    1. Compute the hash:  python -c "from antslab.experiments import arm_definition_hash; print(arm_definition_hash('arm', 'contracts/tinyts_real.toml', 1))"
    2. Add the line to PINNED_HASHES below.
    3. Run the test.
"""
from __future__ import annotations

# arm name -> (hash, date pinned, note)
PINNED_HASHES: dict[str, tuple[str, str, str]] = {
    "portfolio":        ("67618a671563", "2026-10-17", "the server's epoch-2 definition: all nine operators, plain bandit, no reserved pair"),
    "portfolio_screen": ("975051784e2d", "2026-10-17", "portfolio + learned screen promoter"),
    "portfolio_lean":   ("85cf381baf83", "2026-10-17", "noisy operators dropped, reserved evo+qd pair, discounted bandit"),
}
