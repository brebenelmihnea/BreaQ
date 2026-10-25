# BreaQ 2026 — Quantum Dice (biased coin) — GravityFalls

| File | What it is |
|---|---|
| `quantum_dice_biased_coin.ipynb` | The full investigation: threat model, clean source, fault, auditor, fresh evaluation, sensitivity study, response, limitations. |
| `quantum_dice.py` | Command-line program: run the simulated bit service, save the bits, and audit saved runs later. |
| `biased_coin_audit.png` | Figure produced by the notebook. |
| `random_dice.ipynb` | Starting-point notebook: the clean H circuit sampled with `sample_memory`. |

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 1. Generate runs

Each run is a JSON file holding the ordered bits of every request (batch), the source settings and the seed.

```bash
# clean source: |0> -- H -- measure
python quantum_dice.py generate --batches 200 --bits 1000 --seed 1 --out runs/clean.json

# preparation-angle fault, given as P(1) or as an angle error in degrees
python quantum_dice.py generate --p-one 0.55 --batches 200 --bits 1000 --seed 2 --out runs/faulty_055.json
python quantum_dice.py generate --angle-error 3 --batches 200 --bits 1000 --seed 3 --out runs/faulty_3deg.json

# service-layer defect: re-seeding on every request, so every request repeats
python quantum_dice.py generate --replay --batches 50 --bits 1000 --seed 4 --out runs/replay.json
```

Without `--seed`, a random seed is drawn and recorded in the file, so every run can be reproduced.

## 2. Analyze runs

```bash
python quantum_dice.py analyze runs/clean.json runs/faulty_055.json runs/replay.json \
    --report runs/report.csv --plot runs/analysis.png
```

The auditor uses only the bits. It runs three checks on every batch:

| Check | Alarm when | Catches |
|---|---|---|
| Fraction of ones | \|z\| > threshold | biased coin |
| Adjacent transitions | \|z\| > threshold | sticky runs or alternating patterns |
| Replay | the batch is identical to an earlier one | repeated seed, reused output |

The threshold comes from `--alpha` (default 0.01, i.e. |z| > 2.576), the accepted false-alarm rate per check on clean batches. The CSV report has one row per batch with every statistic. The plot shows z per batch in request order plus the distribution.

**Simulation boundary:** the bits come from a classical pseudorandom generator modeling a quantum source. Never use them for passwords, keys or other security purposes.
