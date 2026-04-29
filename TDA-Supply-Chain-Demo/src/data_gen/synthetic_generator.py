"""
data_gen/synthetic_generator.py
================================
Synthetic supply-chain data generator for training, validation, and benchmarking.

Generates GPS-like container tracking events on a realistic network with:
- Normal operation: random delays, seasonal fluctuations
- Disruption scenarios:
    * suez_blockage: global re-routing, massive H1 changes
    * port_congestion: high-weight cluster, increased H0 fragmentation
    * fraud_ring: phantom shipments creating persistent 1-cycles
    * customs_delay: specific edge weight spikes
    * geopolitical_shock: multi-node failure cascade

Each scenario produces known ground truth for benchmarking.
"""

from __future__ import annotations

import hashlib
import math
import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from src.streaming.pipeline import ContainerEvent


# ---------------------------------------------------------------------------
# Network topology builder
# ---------------------------------------------------------------------------

@dataclass
class FacilityNode:
    node_id: int
    name: str
    lat: float
    lon: float
    facility_type: str   # "port", "warehouse", "customs", "hub"
    region: str


def build_realistic_network(n_nodes: int, seed: int = 42) -> List[FacilityNode]:
    """
    Build a synthetic but geographically realistic logistics network.
    Nodes are placed near major trade route hubs.
    """
    rng = random.Random(seed)
    np.random.seed(seed)

    hub_regions = [
        # (lat_center, lon_center, region_name, type)
        (31.2, 121.4, "Asia-Pacific", "port"),
        (1.3, 103.8, "Southeast Asia", "hub"),
        (22.3, 114.2, "East Asia", "port"),
        (51.5, 0.1, "Europe-NW", "port"),
        (53.6, 10.0, "Europe-N", "port"),
        (43.3, 5.4, "Mediterranean", "port"),
        (30.1, 32.3, "Suez Gateway", "customs"),
        (40.7, -74.0, "North America-E", "port"),
        (33.7, -118.2, "North America-W", "port"),
        (-23.9, -46.3, "South America", "hub"),
        (-33.9, 18.4, "Africa-South", "port"),
        (25.7, 55.3, "Middle East", "hub"),
    ]

    nodes = []
    for i in range(n_nodes):
        region_data = hub_regions[i % len(hub_regions)]
        lat_c, lon_c, region, ftype = region_data
        lat = lat_c + rng.gauss(0, 2.0)
        lon = lon_c + rng.gauss(0, 3.0)
        node = FacilityNode(
            node_id=i,
            name=f"{region}-{ftype}-{i:03d}",
            lat=lat,
            lon=lon,
            facility_type=ftype,
            region=region,
        )
        nodes.append(node)
    return nodes


def build_trade_routes(nodes: List[FacilityNode],
                        connectivity: float = 0.15,
                        seed: int = 42) -> List[Tuple[int, int, float]]:
    """
    Build a sparse trade-route graph. Returns list of (u, v, base_weight).
    Geographically closer nodes get lower base weight (faster routes).
    """
    rng = np.random.default_rng(seed)
    n = len(nodes)
    edges = []

    # Ensure connectivity via MST-like seed
    for i in range(n - 1):
        edges.append((i, i + 1, rng.uniform(0.05, 0.2)))

    # Random additional edges
    n_extra = int(connectivity * n * (n - 1) / 2)
    for _ in range(n_extra):
        u, v = rng.choice(n, size=2, replace=False)
        dist = math.sqrt((nodes[u].lat - nodes[v].lat) ** 2 +
                         (nodes[u].lon - nodes[v].lon) ** 2)
        base_w = min(0.3, dist / 200.0)
        edges.append((int(u), int(v), base_w))

    return edges


# ---------------------------------------------------------------------------
# Synthetic data generator
# ---------------------------------------------------------------------------

class SyntheticDataGenerator:
    """
    Generates batches of ContainerEvents for each simulation step.
    Supports injecting ground-truth disruptions for benchmark validation.
    """

    # Known disruption scenarios with expected topological signatures
    SCENARIOS = {
        "suez_blockage": {
            "description": "Major canal blockage forces global rerouting",
            "affected_nodes_fraction": 0.3,
            "delay_multiplier": 8.0,
            "status": "delayed",
            "expected_h1_increase": True,   # New routing cycles appear
            "expected_h0_increase": True,    # Network fragments
        },
        "port_congestion": {
            "description": "Port cluster congestion from weather event",
            "affected_nodes_fraction": 0.15,
            "delay_multiplier": 3.0,
            "status": "delayed",
            "expected_h1_increase": False,
            "expected_h0_increase": False,
        },
        "fraud_ring": {
            "description": "Phantom shipments creating artificial cargo cycles",
            "affected_nodes_fraction": 0.08,
            "delay_multiplier": 0.0,          # No delay — fraud is covert
            "status": "in_transit",
            "expected_h1_increase": True,    # Phantom 1-cycles appear
            "expected_h0_increase": False,
        },
        "customs_delay": {
            "description": "Customs inspection bottleneck at major hub",
            "affected_nodes_fraction": 0.05,
            "delay_multiplier": 5.0,
            "status": "customs_hold",
            "expected_h1_increase": False,
            "expected_h0_increase": False,
        },
        "geopolitical_shock": {
            "description": "Trade route closure due to geopolitical event",
            "affected_nodes_fraction": 0.4,
            "delay_multiplier": 10.0,
            "status": "seized",
            "expected_h1_increase": False,
            "expected_h0_increase": True,    # Network splits
        },
    }

    def __init__(self,
                 n_nodes: int = 50,
                 containers_per_step: int = 20,
                 seed: int = 42):
        self.rng = random.Random(seed)
        self.np_rng = np.random.default_rng(seed)
        self.nodes = build_realistic_network(n_nodes, seed=seed)
        self.routes = build_trade_routes(self.nodes, seed=seed)
        self.containers_per_step = containers_per_step
        self._container_counter = 0

    def _new_container_id(self) -> str:
        self._container_counter += 1
        return f"CONT{self._container_counter:08d}"

    def generate_step(self,
                       step: int,
                       disrupted: bool = False,
                       scenario: str = "suez_blockage") -> List[ContainerEvent]:
        """
        Generate a batch of events for one time step.
        If disrupted=True, inject the specified scenario.
        """
        events = []
        scenario_cfg = self.SCENARIOS.get(scenario, self.SCENARIOS["suez_blockage"])

        n_affected = max(1, int(len(self.routes) * scenario_cfg["affected_nodes_fraction"]))
        affected_routes = set(
            self.rng.randint(0, len(self.routes) - 1)
            for _ in range(n_affected)
        )

        for _ in range(self.containers_per_step):
            route_idx = self.rng.randint(0, len(self.routes) - 1)
            u, v, base_w = self.routes[route_idx]
            node_u = self.nodes[u]
            node_v = self.nodes[v]

            # Interpolated position along route
            alpha = self.rng.uniform(0, 1)
            lat = node_u.lat + alpha * (node_v.lat - node_u.lat)
            lon = node_u.lon + alpha * (node_v.lon - node_u.lon)

            # Normal operational noise
            base_delay = max(0.0, self.np_rng.normal(2.0, 1.0))  # hours
            status = "in_transit"

            # Apply disruption
            if disrupted and route_idx in affected_routes:
                delay_mult = scenario_cfg["delay_multiplier"]
                base_delay = max(0.0, base_delay * delay_mult +
                                  self.np_rng.normal(0, 2.0))
                status = scenario_cfg["status"]

                # Fraud ring: add phantom shipments on unusual routes
                if scenario == "fraud_ring":
                    base_delay = 0.0
                    # Create circular routes (phantom 1-cycles)
                    w = v
                    v = (v + 3) % len(self.nodes)  # Artificial loop

            events.append(ContainerEvent(
                container_id=self._new_container_id(),
                timestamp=time.time() + step * 30.0,
                origin_facility=u,
                destination_facility=v,
                lat=lat + self.np_rng.normal(0, 0.01),
                lon=lon + self.np_rng.normal(0, 0.01),
                status=status,
                delay_hours=base_delay,
                cargo_volume=self.np_rng.uniform(0.1, 1.0),
            ))

        return events

    def generate_benchmark(self,
                            scenario: str = "suez_blockage",
                            n_normal: int = 30,
                            n_disrupted: int = 20,
                            n_recovery: int = 10) -> List[Dict]:
        """
        Generate a complete benchmark dataset with ground-truth labels.
        Returns a list of dicts: {step, events, disrupted, scenario}.
        """
        steps = []
        # Normal phase
        for i in range(n_normal):
            steps.append({
                "step": i,
                "events": self.generate_step(i, disrupted=False),
                "disrupted": False,
                "scenario": "normal",
            })
        # Disruption phase
        for i in range(n_disrupted):
            steps.append({
                "step": n_normal + i,
                "events": self.generate_step(n_normal + i, disrupted=True,
                                              scenario=scenario),
                "disrupted": True,
                "scenario": scenario,
            })
        # Recovery phase (gradual return to normal)
        for i in range(n_recovery):
            frac = i / n_recovery
            events = self.generate_step(n_normal + n_disrupted + i,
                                         disrupted=frac < 0.5,
                                         scenario=scenario)
            steps.append({
                "step": n_normal + n_disrupted + i,
                "events": events,
                "disrupted": frac < 0.5,
                "scenario": "recovery",
            })
        return steps

    def export_csv(self, steps: List[Dict], path: str):
        """Export benchmark dataset as CSV."""
        import csv
        rows = []
        for s in steps:
            for e in s["events"]:
                row = e.to_dict()
                row["step"] = s["step"]
                row["ground_truth_disrupted"] = s["disrupted"]
                row["ground_truth_scenario"] = s["scenario"]
                rows.append(row)

        if rows:
            with open(path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
        return len(rows)
