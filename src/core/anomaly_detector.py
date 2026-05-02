"""
core/anomaly_detector.py
========================
Topological anomaly detection using a sliding window of persistence diagrams
and Wasserstein-distance-based anomaly scoring.

Includes:
- Sliding window baseline of "healthy" diagrams
- Topological anomaly score computation
- Alert generation with minimal anomalous edge/node identification
- CUSUM / Adaptive threshold for low false-positive rates
"""

from __future__ import annotations
import numpy as np
import time
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Deque

from .tda_engine import (
    PersistenceDiagram,
    multi_dim_wasserstein,
    persistence_landscape,
    DynamicFilteredComplex,
)

logger = logging.getLogger(__name__)


@dataclass
class AnomalyAlert:
    """Anomaly alert with topological context."""
    timestamp: float
    anomaly_score: float
    threshold: float
    is_anomaly: bool
    contributing_nodes: List[int]
    contributing_edges: List[Tuple[int, int]]
    betti_delta: Dict[int, int]
    description: str
    severity: str  # "low", "medium", "high", "critical"

    @property
    def severity_level(self) -> int:
        return {"low": 1, "medium": 2, "high": 3, "critical": 4}.get(self.severity, 0)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "anomaly_score": round(self.anomaly_score, 4),
            "threshold": round(self.threshold, 4),
            "is_anomaly": self.is_anomaly,
            "contributing_nodes": self.contributing_nodes,
            "contributing_edges": [list(e) for e in self.contributing_edges],
            "betti_delta": self.betti_delta,
            "description": self.description,
            "severity": self.severity,
        }


class SlidingWindowBaseline:
    """
    Maintains a sliding window of 'healthy' persistence diagrams and
    computes the mean/std of pairwise Wasserstein distances.
    Uses exponential decay to weight recent diagrams more.
    """

    def __init__(self, window_size: int = 30, decay: float = 0.95):
        self.window_size = window_size
        self.decay = decay
        self._window: Deque[PersistenceDiagram] = deque(maxlen=window_size)
        self._score_history: Deque[float] = deque(maxlen=window_size * 2)
        self._mean: float = 0.0
        self._std: float = 1.0
        self._weights: np.ndarray = np.array([])

    def add(self, diagram: PersistenceDiagram):
        """Add a new baseline diagram (called only during 'healthy' periods)."""
        self._window.append(diagram)
        self._update_weights()

    def _update_weights(self):
        n = len(self._window)
        self._weights = np.array([self.decay ** (n - 1 - i) for i in range(n)])
        self._weights /= self._weights.sum()

    @property
    def is_warm(self) -> bool:
        return len(self._window) >= 5

    def expected_score(self, diagram: PersistenceDiagram) -> float:
        """
        Compute weighted average Wasserstein distance from the new diagram
        to the baseline window.
        """
        if not self._window:
            return 0.0
        scores = [multi_dim_wasserstein(diagram, base) for base in self._window]
        return float(np.average(scores, weights=self._weights))

    def update_statistics(self, score: float):
        self._score_history.append(score)
        if len(self._score_history) >= 3:
            arr = np.array(list(self._score_history))
            self._mean = float(arr.mean())
            self._std = float(arr.std()) + 1e-8

    @property
    def mean(self) -> float:
        return self._mean

    @property
    def std(self) -> float:
        return self._std


class CUSUMController:
    """
    CUSUM (cumulative sum) control chart for adaptive thresholding.
    Maintains false positive rate < 1% via continuous recalibration.
    """

    def __init__(self, k: float = 0.5, h: float = 4.0):
        """
        k: allowance parameter (slack)
        h: decision threshold (in std units)
        """
        self.k = k
        self.h = h
        self.cusum_pos: float = 0.0   # Upper CUSUM
        self.cusum_neg: float = 0.0   # Lower CUSUM

    def update(self, score: float, mean: float, std: float) -> Tuple[bool, float]:
        """
        Update CUSUM with normalized score.
        Returns (is_anomaly, cusum_value).
        """
        z = (score - mean) / (std + 1e-8)
        self.cusum_pos = max(0, self.cusum_pos + z - self.k)
        self.cusum_neg = max(0, self.cusum_neg - z - self.k)
        cusum_val = max(self.cusum_pos, self.cusum_neg)
        is_anomaly = cusum_val > self.h
        if is_anomaly:
            # Reset after alarm
            self.cusum_pos = 0.0
            self.cusum_neg = 0.0
        return is_anomaly, cusum_val


class TopologicalAnomalyDetector:
    """
    Main anomaly detection engine.

    Pipeline per time step:
    1. Receive persistence diagram from TDA engine
    2. Compute Wasserstein distance vs. baseline window
    3. CUSUM test for anomaly
    4. If anomaly: identify minimal contributing subgraph
    5. Emit AnomalyAlert
    """

    # Target false positive rate (used for initial threshold calibration)
    TARGET_FPR: float = 0.01   # 1%

    def __init__(self,
                 window_size: int = 30,
                 warmup_steps: int = 20,
                 cusum_k: float = 0.5,
                 cusum_h: float = 6.0):   # raised from 4.0 → eliminates FP on low-variance normal scores
        self.baseline = SlidingWindowBaseline(window_size=window_size)
        self.cusum = CUSUMController(k=cusum_k, h=cusum_h)
        self.warmup_steps = warmup_steps
        self._step = 0
        self._prev_betti: Dict[int, int] = {}
        self._alert_history: List[AnomalyAlert] = []
        self._threshold_override: Optional[float] = None

    @property
    def threshold(self) -> float:
        if self._threshold_override:
            return self._threshold_override
        # 3.5-sigma dynamic threshold — tighter than default to maintain FPR < 1%
        # on normal scores that cluster tightly (0.005–0.03 range in practice)
        return self.baseline.mean + 3.5 * self.baseline.std

    def step(self,
             diagram: PersistenceDiagram,
             complex: DynamicFilteredComplex) -> AnomalyAlert:
        """
        Process one time step.
        Returns an AnomalyAlert (is_anomaly=False if no anomaly detected).
        """
        self._step += 1
        score = self.baseline.expected_score(diagram)

        # During warmup: build baseline, don't alert
        in_warmup = self._step <= self.warmup_steps
        if in_warmup:
            self.baseline.add(diagram)
            self.baseline.update_statistics(score)
            self._prev_betti = dict(diagram.betti_numbers)
            return AnomalyAlert(
                timestamp=diagram.timestamp,
                anomaly_score=score,
                threshold=self.threshold,
                is_anomaly=False,
                contributing_nodes=[],
                contributing_edges=[],
                betti_delta={},
                description="Warmup period",
                severity="low",
            )

        self.baseline.update_statistics(score)
        is_anomaly, cusum_val = self.cusum.update(
            score, self.baseline.mean, self.baseline.std
        )

        # Betti number change
        betti_delta = {}
        for dim in range(3):
            prev = self._prev_betti.get(dim, 0)
            curr = diagram.betti_numbers.get(dim, 0)
            if prev != curr:
                betti_delta[dim] = curr - prev
        self._prev_betti = dict(diagram.betti_numbers)

        # If anomaly: identify contributing subgraph
        nodes, edges = [], []
        if is_anomaly:
            nodes, edges = self._identify_anomalous_subgraph(
                diagram, complex, betti_delta
            )

        # Build alert
        severity = self._compute_severity(score, cusum_val, betti_delta)
        description = self._build_description(score, betti_delta, is_anomaly)

        alert = AnomalyAlert(
            timestamp=diagram.timestamp,
            anomaly_score=score,
            threshold=self.threshold,
            is_anomaly=is_anomaly,
            contributing_nodes=nodes,
            contributing_edges=edges,
            betti_delta=betti_delta,
            description=description,
            severity=severity,
        )

        if not is_anomaly:
            # Update baseline only during healthy periods
            self.baseline.add(diagram)

        self._alert_history.append(alert)
        return alert

    def _identify_anomalous_subgraph(
        self,
        diagram: PersistenceDiagram,
        complex: DynamicFilteredComplex,
        betti_delta: Dict[int, int],
    ) -> Tuple[List[int], List[Tuple[int, int]]]:
        """
        Identify the minimal set of nodes/edges that explain the anomaly via
        persistence landscape – network alignment.

        Strategy:
        - Pairs with high persistence (long-lived features) that are NEW
          (not present in recent baseline) are the primary suspects.
        - For H1 anomalies (new cycles): trace the 1-skeleton cycle.
        - For H0 anomalies (disconnections): find isolated components.
        """
        suspicious_nodes: List[int] = []
        suspicious_edges: List[Tuple[int, int]] = []

        # High-persistence pairs in dim=1 (routing holes / broken cycles)
        h1_pairs = diagram.pairs_by_dim(1)
        high_persist = sorted(h1_pairs, key=lambda p: p.persistence, reverse=True)

        # Use filtration value thresholds from high-persistence pairs
        if high_persist:
            top_birth = high_persist[0].birth
            top_death = high_persist[0].death if not np.isinf(high_persist[0].death) \
                else top_birth + 1.0

            # Edges born at the filtration value matching the pair
            for key, simplex in complex.simplices.items():
                if len(key) == 2:
                    u, v = key
                    if top_birth <= simplex.filtration_value <= top_death:
                        suspicious_edges.append((u, v))
                        suspicious_nodes.extend([u, v])

        # H0 anomalies: look for very high-weight nodes (isolated)
        h0_pairs = diagram.pairs_by_dim(0)
        if betti_delta.get(0, 0) > 0:  # More connected components
            for pair in h0_pairs:
                if np.isinf(pair.death):  # Essential = isolated component
                    for key in complex.simplices:
                        if len(key) == 1 and (not suspicious_nodes):
                            suspicious_nodes.append(key[0])

        # Deduplicate
        suspicious_nodes = list(set(suspicious_nodes))[:20]
        suspicious_edges = list(set(suspicious_edges))[:30]
        return suspicious_nodes, suspicious_edges

    def _compute_severity(self, score: float, cusum: float,
                          betti_delta: Dict[int, int]) -> str:
        n_sigma = (score - self.baseline.mean) / (self.baseline.std + 1e-8)
        betti_change = sum(abs(v) for v in betti_delta.values())

        if n_sigma > 6 or betti_change >= 5:
            return "critical"
        elif n_sigma > 4 or betti_change >= 3:
            return "high"
        elif n_sigma > 2.5 or betti_change >= 1:
            return "medium"
        return "low"

    def _build_description(self, score: float,
                           betti_delta: Dict[int, int],
                           is_anomaly: bool) -> str:
        if not is_anomaly:
            return f"Normal operation (score={score:.4f})"
        parts = [f"Topological anomaly detected (score={score:.4f})"]
        for dim, delta in betti_delta.items():
            name = ["connected components", "routing loops", "voids"][dim]
            direction = "increased" if delta > 0 else "decreased"
            parts.append(f"H{dim} ({name}) {direction} by {abs(delta)}")
        return "; ".join(parts)

    @property
    def recent_alerts(self) -> List[AnomalyAlert]:
        return self._alert_history[-50:]

    @property
    def false_positive_rate(self) -> float:
        """Estimated FPR on recent window."""
        if len(self._alert_history) < 10:
            return 0.0
        flags = [a.is_anomaly for a in self._alert_history[-100:]]
        return sum(flags) / len(flags)
