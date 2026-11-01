# Model retention

A leaderboard is a read-only view. Preservation runs as a separate process so opening the interface cannot trigger training or mutate evidence.

## Available now

`ants candidates sync` reads a verified ledger and preserves the best deepest-stage feasible specification per cap in an immutable version record. A current pointer can improve inside the same run/arm/epoch/contract scope; old versions remain. `watch` repeats this operation at an explicit interval. Neither action retains weights or installs itself as a service.

`ants candidates refit` creates a separately labelled training-only bundle for an explicitly chosen development dataset and horizon: numeric weights, preprocessing, manifest, source, checksums and prediction parity. Each member's weights and preprocessing count toward the physical cap. An export that exceeds the cap is refused. Historical validation metrics remain attached to the specification and must not be attributed to the new weights.

The November 2 E43 acceptance retained 128 candidate records over 32 completed runs. Best specifications by nominal cap are linked from the final report. Private weights and ledgers remain on the owner's Mac.

## Required before a future unattended training rollout

1. At evaluation completion, preserve a candidate's actual fitted state before the subprocess exits, with dataset, horizon, fold, training-window and seed identities. Never request sealed data to populate a checkpoint.
2. Return an artifact receipt alongside metrics. A controller validates hashes, physical storage and prediction parity before updating a best-model pointer. Mark pending, saved, rejected or failed explicitly; a failure must not silently claim that weights exist.
3. Show the selected row's model receipt in Leaderboard: source run/trial, nominal versus stored parameter counts, checkpoint versus refit, available folds, local backup status and export path. Preserve selection by epoch and scope.
4. Pin verified best-model versions against disk cleanup. Clean obsolete raw intermediates only after verified backup; retain the evidence ledger, manifests and winning version history.
5. Back up bundles with ledger/source/environment identities to the local machine before deployment upgrades. Test restore and inference from a separate process. An archive file existing is not proof of recovery.

This is an explicit remaining engineering gate, not a feature claimed to be installed on the E43 server. Complete it before the next series if that series is intended to yield releasable trained models. It can proceed independently of E43's concluded scientific question.
