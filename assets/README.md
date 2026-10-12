# assets

Figures, their data and the code that draws them.

| file | what |
|---|---|
| `*.svg` | the figures used in the README and docs; generated, do not edit by hand |
| `make_figures.py` | draws every figure; standard library only: `python assets/make_figures.py` |
| `measure_frontier.py` | measures the reference points and reads the best specs from a ledger into `frontier.json` |
| `scatter.svg`, `frontier.svg` | parameters versus error (axes, caps, step frontier) and skill versus parameters (bars); both read `frontier.json` |
| `frontier.json` | data behind both figures, with its protocol |
| `proxy_fidelity.json` | S0, S1, S2 agreement on 48 random feasible specs (`scripts/proxy_fidelity.py`) |
| `logo.svg`, `logo-wordmark.svg` | the ants mark (two ants and the fixed square head, with antennae) and the mark with the name |
| `p-to-q_logo.png` | the [p → q] mark used in the banner |

The name and the banner headline are set in Martina Plantijn when it is installed and fall back to Crimson, Source Serif or Georgia otherwise. Martina Plantijn is a commercial typeface, so it is not embedded or redistributed here.

Design rules are at the top of `make_figures.py`.
