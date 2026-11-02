# Tiny neural training gym

`contracts/tiny_nn_synthetic.toml` is a separate, synthetic engineering arena. It compares a ridge forecaster and a 16-input tanh MLP with width 1–8, using a fixed seeded AR process. Train is [0,1024), validation [1024,1280), test is not used for fitting/scoring. Input/target normalization is training-only; validation uses sixteen-step autoregressive rollouts at nonoverlapping origins. Steps are fixed by stage (100/300/600), not selected on validation.

The largest MLP has 145 trainable values and 179 stored values including lag-specific input and target preprocessing; ridge stores 51. Both must fit a physical cap of 300. This is not parameter-count matching, a real-data claim or a production benchmark. It is not an E44b factor.

S2 exports its actual trained state, source, hashes, bounds, seeds, window losses and exact reload rollout parity. Existing attempt outputs are refused. Checkpoints reject checksum/shape/size errors and use NumPy with pickle disabled. The generic protected inventory handles recovery and preservation. No GPU or external model service is needed.
