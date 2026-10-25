# BreaQ 2026 — Quantum Hacking Track, Challenge 1

## Trust No Input

This bundle contains the first full-stack challenge at the application/circuit boundary.

### Participant distribution

Distribute only the `participant/` directory. It contains:

- `01_trust_no_input.ipynb` — challenge notebook;
- `breaq_ch1/` — local mock service;
- `team_instance.json` — demonstration instance (replace `team_id` per team if desired);
- `requirements.txt` — minimal dependencies (the challenge uses Qiskit's portable `BasicSimulator`).

Run from the participant directory:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
jupyter lab 01_trust_no_input.ipynb
```

### Organizer-only material

Do not distribute `organizer/` before the deadline. It contains a worked solution, judging guide, and hidden-test examples.

### Intended duration

2.5–3.5 hours for the core investigation; 1–2 additional hours for the exploit and research extension.
