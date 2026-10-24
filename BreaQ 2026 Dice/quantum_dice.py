"""BreaQ 2026 Quantum Dice: simulate a one-qubit bit service, then audit what it produced.

Two steps, so measurement and analysis are separate from generation:

    python quantum_dice.py generate --batches 200 --bits 1000 --out runs/clean.json
    python quantum_dice.py generate --p-one 0.55 --batches 200 --bits 1000 --out runs/faulty_055.json
    python quantum_dice.py analyze runs/clean.json runs/faulty_055.json --plot runs/analysis.png

Simulation boundary: bits come from Qiskit's Statevector.sample_memory, which uses a
classical pseudorandom generator. This models a quantum-bit source; it does not produce
physical quantum entropy. Never use these bits for passwords, keys or other security purposes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist

import numpy as np
import qiskit
from qiskit import QuantumCircuit
from qiskit.quantum_info import Statevector

FORMAT = "breaq-quantum-dice/1"


# --------------------------------------------------------------------------- sources

def clean_circuit() -> QuantumCircuit:
    # Ideal preparation: |0> -- H gives |+>, with P(0) = P(1) = 1/2.
    qc = QuantumCircuit(1)
    qc.h(0)
    return qc


def faulty_circuit(theta: float) -> QuantumCircuit:
    # Preparation-angle error: Ry(theta) replaces H, so P(1) = sin^2(theta/2).
    qc = QuantumCircuit(1)
    qc.ry(theta, 0)
    return qc


def theta_from_p_one(p_one: float) -> float:
    return 2 * math.asin(math.sqrt(p_one))


class QuantumDice:
    """Mock bit service. Each bit models one fresh preparation and measurement.

    Normally the sampler is seeded once and keeps its state between requests, so every
    request gets fresh draws. With replay=True it is re-seeded on every request (a
    classical service-layer defect): every request then returns the same bits.
    """

    def __init__(self, circuit: QuantumCircuit, seed: int, replay: bool = False):
        self.state = Statevector(circuit)
        self.seed = seed
        self.replay = replay
        self.state.seed(seed)

    def request(self, n_bits: int) -> np.ndarray:
        if self.replay:
            self.state.seed(self.seed)
        return self.state.sample_memory(n_bits).astype(int)


# --------------------------------------------------------------------------- checks
# Every check sees only the ordered bits (and request boundaries), never the source settings.

def z_threshold(alpha: float) -> float:
    # Two-sided threshold for a per-check false-alarm rate alpha on clean batches.
    return NormalDist().inv_cdf(1 - alpha / 2)


def frequency_check(bits: np.ndarray) -> dict:
    """Fraction of ones. For independent fair bits, ones ~ Binomial(n, 1/2)."""
    n = len(bits)
    ones = int(bits.sum())
    return {"ones": ones, "fraction": ones / n, "z": (ones - n / 2) / math.sqrt(n / 4)}


def transition_check(bits: np.ndarray) -> dict:
    """Adjacent bits that differ. For independent fair bits each of the n-1 pairs differs
    with probability 1/2, independently, so transitions ~ Binomial(n-1, 1/2).
    Too few transitions means sticky runs; too many means alternating patterns."""
    m = len(bits) - 1
    t = int(np.sum(bits[1:] != bits[:-1]))
    return {"transitions": t, "transition_rate": t / m, "z": (t - m / 2) / math.sqrt(m / 4)}


def replay_check(batches: list[np.ndarray]) -> list[int | None]:
    """For each batch, the index of an earlier identical batch, or None."""
    first_seen: dict[str, int] = {}
    result = []
    for i, bits in enumerate(batches):
        digest = hashlib.sha256(bits.astype(np.uint8).tobytes()).hexdigest()
        result.append(first_seen.get(digest))
        first_seen.setdefault(digest, i)
    return result


def audit(batches: list[np.ndarray], alpha: float) -> list[dict]:
    threshold = z_threshold(alpha)
    replays = replay_check(batches)
    rows = []
    for i, bits in enumerate(batches):
        freq, trans = frequency_check(bits), transition_check(bits)
        row = {
            "batch": i,
            "bits": len(bits),
            "ones": freq["ones"],
            "fraction_ones": round(freq["fraction"], 5),
            "freq_z": round(freq["z"], 3),
            "freq_alarm": abs(freq["z"]) > threshold,
            "transition_rate": round(trans["transition_rate"], 5),
            "trans_z": round(trans["z"], 3),
            "trans_alarm": abs(trans["z"]) > threshold,
            "replay_of": replays[i],
            "replay_alarm": replays[i] is not None,
        }
        row["any_alarm"] = row["freq_alarm"] or row["trans_alarm"] or row["replay_alarm"]
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- storage

def save_run(path: Path, batches: list[np.ndarray], source: dict, seed: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "format": FORMAT,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "qiskit_version": qiskit.__version__,
        "seed": seed,
        "bits_per_batch": len(batches[0]),
        "source": source,
        "batches": ["".join(map(str, b)) for b in batches],
    }
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def load_run(path: Path) -> tuple[dict, list[np.ndarray]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        raise ValueError(f"{path}: not a {FORMAT} file")
    batches = [np.frombuffer(s.encode(), dtype=np.uint8) - ord("0") for s in data["batches"]]
    return data, [b.astype(int) for b in batches]


# --------------------------------------------------------------------------- commands

def cmd_generate(args: argparse.Namespace) -> None:
    if args.angle_error is not None:
        theta = math.pi / 2 + math.radians(args.angle_error)
    elif args.p_one is not None:
        if not 0 <= args.p_one <= 1:
            sys.exit("--p-one must be between 0 and 1")
        theta = theta_from_p_one(args.p_one)
    else:
        theta = None

    if theta is None:
        circuit, label = clean_circuit(), "clean"
    else:
        circuit, label = faulty_circuit(theta), "faulty"
    if args.replay:
        label = "faulty"

    # Without --seed, draw one from the OS and record it so the run stays reproducible.
    seed = args.seed if args.seed is not None else int(np.random.SeedSequence().entropy % 2**32)
    p_one = float(Statevector(circuit).probabilities()[1])
    source = {
        "label": label,
        "circuit": "h" if theta is None else f"ry({theta:.6f})",
        "p_one": round(p_one, 6),
        "angle_error_deg": 0.0 if theta is None else round(math.degrees(theta - math.pi / 2), 4),
        "replay": args.replay,
    }

    service = QuantumDice(circuit, seed, replay=args.replay)
    batches = [service.request(args.bits) for _ in range(args.batches)]
    save_run(Path(args.out), batches, source, seed)

    total = args.batches * args.bits
    ones = sum(int(b.sum()) for b in batches)
    print(f"wrote {args.out}: {args.batches} batches x {args.bits} bits = {total} bits")
    print(f"  source: {source['circuit']}, P(1) = {source['p_one']:.4f}, "
          f"angle error = {source['angle_error_deg']:+.2f} deg, replay = {args.replay}, seed = {seed}")
    print(f"  observed fraction of ones: {ones / total:.4f}")


def cmd_analyze(args: argparse.Namespace) -> None:
    threshold = z_threshold(args.alpha)
    print(f"Auditor: alarm when |z| > {threshold:.3f} (alpha = {args.alpha:g} per check), "
          f"or when a batch repeats an earlier one.")
    print("Checks use only the bits; the source label is shown only to name the alarms.\n")

    header = (f"{'file':<24} {'label':<7} {'batches':>7} {'bits':>9} {'ones':>7} "
              f"{'freq':>6} {'trans':>6} {'replay':>6} {'any':>6}   meaning")
    print(header)
    print("-" * len(header))

    results = []
    all_rows = []
    for path in map(Path, args.files):
        data, batches = load_run(path)
        rows = audit(batches, args.alpha)
        label = data["source"]["label"]
        results.append((path, data, rows))
        for r in rows:
            all_rows.append({"file": path.name, "label": label, **r})

        n = len(rows)
        count = {k: sum(r[k] for r in rows) for k in ("freq_alarm", "trans_alarm", "replay_alarm", "any_alarm")}
        bits = sum(r["bits"] for r in rows)
        ones = sum(r["ones"] for r in rows) / bits
        meaning = "false alarms" if label == "clean" else f"detections, {n - count['any_alarm']} missed"
        print(f"{path.name[:24]:<24} {label:<7} {n:>7} {bits:>9} {ones:>7.4f} "
              f"{count['freq_alarm']:>6} {count['trans_alarm']:>6} {count['replay_alarm']:>6} "
              f"{count['any_alarm']:>6}   {meaning}")

    print("\nColumns: freq = fraction-of-ones alarms, trans = adjacent-transition alarms, "
          "replay = batches identical to an earlier one, any = batches with at least one alarm.")
    print(f"With two z checks per batch, clean data should raise 'any' alarms on about "
          f"{2 * args.alpha:.0%} of batches.")

    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(all_rows[0]))
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"\nPer-batch report: {report}")

    if args.plot:
        plot_results(results, threshold, Path(args.plot))
        print(f"Plot: {args.plot}")


# --------------------------------------------------------------------------- plot

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, INK_2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"


def plot_results(results: list, threshold: float, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if len(results) > len(SERIES):
        print(f"Plotting the first {len(SERIES)} files only (one fixed color per file).")
        results = results[: len(SERIES)]

    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "axes.edgecolor": AXIS,
        "axes.labelcolor": INK_2, "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK,
        "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    })
    fig, axes = plt.subplots(1, 3, figsize=(16, 4.8))
    panels = [("freq_z", "fraction-of-ones z"), ("trans_z", "adjacent-transition z")]

    # Left and middle: z per batch in request order, so drift or sudden changes are visible.
    for ax, (key, name) in zip(axes[:2], panels):
        for (run_path, data, rows), color in zip(results, SERIES):
            ax.plot([r["batch"] for r in rows], [r[key] for r in rows], "o", ms=3.5, alpha=0.75,
                    color=color, label=f"{run_path.stem} ({data['source']['label']})")
        for s in (-1, 1):
            ax.axhline(s * threshold, color=INK_2, ls="--", lw=1)
        ax.set_xlabel("batch (request order)")
        ax.set_ylabel(name)
        ax.set_title(f"{name} per batch\nalarm outside the dashed lines", fontsize=11)
        ax.grid(color=GRID, lw=0.6)

    # Right: distribution of the fraction-of-ones z for each file.
    ax = axes[2]
    all_z = [r["freq_z"] for _, _, rows in results for r in rows]
    bins = np.linspace(min(all_z + [-threshold]) - 0.5, max(all_z + [threshold]) + 0.5, 50)
    for (run_path, data, rows), color in zip(results, SERIES):
        ax.hist([r["freq_z"] for r in rows], bins=bins, alpha=0.6, color=color, edgecolor=SURFACE, lw=0.8,
                label=f"{run_path.stem} ({data['source']['label']})")
    for s in (-1, 1):
        ax.axvline(s * threshold, color=INK_2, ls="--", lw=1)
    ax.set_xlabel("fraction-of-ones z")
    ax.set_ylabel("number of batches")
    ax.set_title("Distribution of fraction-of-ones z", fontsize=11)
    ax.grid(axis="y", color=GRID, lw=0.6)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=min(4, len(labels)), frameon=False, fontsize=9)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, facecolor=SURFACE)


# --------------------------------------------------------------------------- CLI

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="run the simulated bit service and save the ordered bits")
    fault = gen.add_mutually_exclusive_group()
    fault.add_argument("--p-one", type=float, help="faulty source with this P(1) (Ry preparation)")
    fault.add_argument("--angle-error", type=float, metavar="DEG",
                       help="faulty source with this preparation-angle error in degrees")
    gen.add_argument("--replay", action="store_true",
                     help="re-seed on every request, so all requests repeat (service-layer defect)")
    gen.add_argument("--batches", type=int, default=100, help="number of requests (default 100)")
    gen.add_argument("--bits", type=int, default=1000, help="bits per request (default 1000)")
    gen.add_argument("--seed", type=int, help="seed for the sampler (default: random, recorded in the file)")
    gen.add_argument("--out", required=True, help="output JSON file")
    gen.set_defaults(func=cmd_generate)

    ana = sub.add_parser("analyze", help="audit one or more saved runs")
    ana.add_argument("files", nargs="+", help="run files written by 'generate'")
    ana.add_argument("--alpha", type=float, default=0.01, help="false-alarm rate per check (default 0.01)")
    ana.add_argument("--report", help="write a per-batch CSV report here")
    ana.add_argument("--plot", help="write a PNG figure here")
    ana.set_defaults(func=cmd_analyze)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
