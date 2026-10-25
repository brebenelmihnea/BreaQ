from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
import json
from pathlib import Path
from typing import Iterable

from qiskit import QuantumCircuit, transpile
from qiskit.providers.basic_provider import BasicSimulator

CANONICAL_SLOTS = ("alpha", "beta", "gamma", "delta")


def _validate_bits(bits: str, n: int = 4) -> None:
    if len(bits) != n or any(bit not in "01" for bit in bits):
        raise ValueError(f"Expected a {n}-bit binary string")


def _validate_map(order: Iterable[int], n: int = 4) -> tuple[int, ...]:
    order = tuple(int(i) for i in order)
    if sorted(order) != list(range(n)):
        raise ValueError("compatibility_map must be a permutation")
    return order


def build_bv_probe(fingerprint: str) -> QuantumCircuit:
    """Build a Bernstein-Vazirani probe.

    fingerprint[i] is encoded on Qiskit qubit i. Measurement keys are displayed
    in Qiskit's classical-register order, with the highest classical bit leftmost.
    """
    _validate_bits(fingerprint)
    n = len(fingerprint)
    qc = QuantumCircuit(n + 1, n, name="policy_probe")
    ancilla = n
    qc.x(ancilla)
    qc.h(ancilla)
    for qubit in range(n):
        qc.h(qubit)
    qc.barrier()
    for qubit, bit in enumerate(fingerprint):
        if bit == "1":
            qc.cx(qubit, ancilla)
    qc.barrier()
    for qubit in range(n):
        qc.h(qubit)
    qc.measure(range(n), range(n))
    return qc


def dominant_key(counts: dict[str, int]) -> str:
    return max(counts, key=counts.get).replace(" ", "")


def canonical_bits_from_counts(counts: dict[str, int]) -> str:
    """Trusted q0..q(n-1) interpretation of a Qiskit count key."""
    return dominant_key(counts)[::-1]


def compatibility_decode(counts: dict[str, int], compatibility_map: Iterable[int]) -> str:
    """Legacy reporting adapter. Intentionally unsafe for security decisions."""
    canonical = canonical_bits_from_counts(counts)
    order = _validate_map(compatibility_map, len(canonical))
    return "".join(canonical[index] for index in order)


@dataclass(frozen=True)
class CaseSummary:
    case_id: str
    registry_fingerprint: str
    provenance: str


class ChallengeService:
    """Local mock-up of the BreaQ policy-fingerprint application."""

    def __init__(self, instance_path: str | Path):
        data = json.loads(Path(instance_path).read_text(encoding="utf-8"))
        self.team_id = data["team_id"]
        self._cases = {case["case_id"]: case for case in data["cases"]}
        self._simulator = BasicSimulator()

    def list_cases(self) -> list[CaseSummary]:
        return [
            CaseSummary(k, v["registry_fingerprint"], v["provenance"])
            for k, v in self._cases.items()
        ]

    def get_manifest(self, case_id: str) -> dict:
        case = self._cases[case_id]
        return {
            "case_id": case_id,
            "schema": case["schema"],
            "logical_slots": list(CANONICAL_SLOTS),
            "compatibility_map": list(case["compatibility_map"]),
            "provenance": case["provenance"],
        }

    def get_circuit(self, case_id: str) -> QuantumCircuit:
        return build_bv_probe(self._cases[case_id]["oracle_fingerprint"])

    def execute(self, case_id: str, shots: int = 512, seed: int = 2026) -> dict:
        case = self._cases[case_id]
        circuit = self.get_circuit(case_id)
        compiled = transpile(circuit, self._simulator, optimization_level=0)
        result = self._simulator.run(compiled, shots=shots, seed_simulator=seed).result()
        counts = dict(result.get_counts())
        reported = compatibility_decode(counts, case["compatibility_map"])
        return {
            "case_id": case_id,
            "counts": counts,
            "reported_fingerprint": reported,
            "registry_fingerprint": case["registry_fingerprint"],
            "decision": "MATCH" if reported == case["registry_fingerprint"] else "MISMATCH",
        }

    def simulate_adapter(self, fingerprint: str, compatibility_map: Iterable[int], shots: int = 512) -> dict:
        _validate_bits(fingerprint)
        order = _validate_map(compatibility_map, len(fingerprint))
        circuit = build_bv_probe(fingerprint)
        compiled = transpile(circuit, self._simulator, optimization_level=0)
        result = self._simulator.run(compiled, shots=shots, seed_simulator=2026).result()
        counts = dict(result.get_counts())
        return {
            "counts": counts,
            "canonical_fingerprint": canonical_bits_from_counts(counts),
            "reported_fingerprint": compatibility_decode(counts, order),
        }


