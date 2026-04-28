"""
streaming/pipeline.py
======================
Real-time streaming pipeline:
- Ingests GPS-like container tracking events
- Maintains a live supply-chain graph (in-memory + optional RedisGraph)
- Triggers TDA computation every TICK_SECONDS
- Emits anomaly alerts within 5 minutes of disruption onset

Architecture:
  ContainerEvent → EventBuffer → GraphUpdater → TDAEngine → AnomalyDetector → AlertBus
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional

from src.core.tda_engine import (
    DynamicFilteredComplex,
    PersistentHomologyComputer,
    PersistenceDiagram,
)
from src.core.anomaly_detector import TopologicalAnomalyDetector, AnomalyAlert

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

@dataclass
class ContainerEvent:
    """A single GPS / status event from a shipping container."""
    container_id: str
    timestamp: float                  # Unix epoch
    origin_facility: int              # Node ID
    destination_facility: int         # Node ID
    lat: float
    lon: float
    status: str                       # "in_transit", "delayed", "arrived", "customs_hold"
    delay_hours: float = 0.0          # Positive = delayed
    cargo_volume: float = 1.0         # Normalized 0-1

    @classmethod
    def from_dict(cls, d: Dict) -> "ContainerEvent":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> Dict:
        return asdict(self)

    @property
    def edge_weight(self) -> float:
        """
        Convert event status/delay to a filtration weight.
        Higher weight = more anomalous / congested route.
        """
        base = self.delay_hours / 24.0   # Normalise to days
        status_penalty = {
            "in_transit": 0.0,
            "arrived": 0.0,
            "delayed": 0.3,
            "customs_hold": 0.5,
            "seized": 0.8,
            "unknown": 0.6,
        }.get(self.status, 0.2)
        return min(1.0, base + status_penalty)


# ---------------------------------------------------------------------------
# Graph updater — converts events to complex updates
# ---------------------------------------------------------------------------

class GraphUpdater:
    """
    Maintains a live supply-chain graph from streaming container events.
    Aggregates multiple containers per route into a single edge weight
    (exponential moving average of delay).
    """

    EMA_ALPHA = 0.3   # EMA decay for edge weights

    def __init__(self, complex: DynamicFilteredComplex):
        self.complex = complex
        self._edge_ema: Dict[tuple, float] = {}
        self._active_containers: Dict[str, ContainerEvent] = {}
        self._event_count = 0

    def apply(self, event: ContainerEvent):
        """Apply a container event to the live complex."""
        self._event_count += 1
        self._active_containers[event.container_id] = event

        u, v = event.origin_facility, event.destination_facility
        edge_key = (min(u, v), max(u, v))

        # EMA of edge weight
        prev = self._edge_ema.get(edge_key, 0.0)
        new_weight = self.EMA_ALPHA * event.edge_weight + (1 - self.EMA_ALPHA) * prev
        self._edge_ema[edge_key] = new_weight

        # Update nodes
        import numpy as np
        self.complex.update_node(u)
        self.complex.update_node(v)

        # Update or remove edge
        if event.status in ("seized",):
            self.complex.remove_edge(u, v)
        else:
            self.complex.update_edge(u, v, weight=new_weight)

    @property
    def n_events(self) -> int:
        return self._event_count

    @property
    def active_routes(self) -> int:
        return len(self._edge_ema)


# ---------------------------------------------------------------------------
# Optional RedisGraph backend
# ---------------------------------------------------------------------------

class RedisGraphStore:
    """
    Optional persistence layer using RedisGraph (Falkor).
    Falls back gracefully if redis is not available.
    """

    def __init__(self, host: str = "localhost", port: int = 6379,
                 graph_name: str = "supply_chain"):
        self.available = False
        self.graph_name = graph_name
        try:
            import redis
            self._client = redis.Redis(host=host, port=port, decode_responses=True)
            self._client.ping()
            # Try RedisGraph / FalkorDB
            try:
                from redis.commands.graph import Graph
                self._graph = Graph(self._client, graph_name)
                self.available = True
                logger.info("RedisGraph backend connected")
            except Exception:
                logger.warning("RedisGraph module not available; using in-memory only")
        except Exception as e:
            logger.warning(f"Redis not available ({e}); using in-memory only")

    def upsert_node(self, node_id: int, props: Dict):
        if not self.available:
            return
        try:
            q = f"MERGE (n:Facility {{id: {node_id}}}) SET n += $props"
            self._graph.query(q, {"props": props})
        except Exception as e:
            logger.debug(f"RedisGraph node upsert failed: {e}")

    def upsert_edge(self, u: int, v: int, weight: float):
        if not self.available:
            return
        try:
            q = (f"MATCH (a:Facility {{id: {u}}}), (b:Facility {{id: {v}}}) "
                 f"MERGE (a)-[r:CARGO_FLOW]->(b) SET r.weight = {weight:.4f}")
            self._graph.query(q)
        except Exception as e:
            logger.debug(f"RedisGraph edge upsert failed: {e}")


# ---------------------------------------------------------------------------
# Main streaming pipeline
# ---------------------------------------------------------------------------

class StreamingPipeline:
    """
    Streaming pipeline that processes container events in real-time.

    Threading model:
    - Ingest thread: receives events from queue
    - TDA thread: runs homology computation on a tick schedule
    - Alert thread: dispatches alerts to registered callbacks

    Target: detect anomalies within 5 minutes (300 seconds).
    """

    DEFAULT_TICK_SECONDS = 30        # Compute TDA every 30s → 10 ticks in 5 min
    DEFAULT_WINDOW_SIZE = 30
    DEFAULT_WARMUP = 20

    def __init__(self,
                 tick_seconds: float = DEFAULT_TICK_SECONDS,
                 window_size: int = DEFAULT_WINDOW_SIZE,
                 warmup_steps: int = DEFAULT_WARMUP,
                 redis_host: str = "localhost",
                 use_redis: bool = False):
        self.tick_seconds = tick_seconds

        # Core components
        self.complex = DynamicFilteredComplex(witness=False)
        self.homology = PersistentHomologyComputer()
        self.detector = TopologicalAnomalyDetector(
            window_size=window_size,
            warmup_steps=warmup_steps,
        )
        self.graph_updater = GraphUpdater(self.complex)
        self.redis = RedisGraphStore(host=redis_host) if use_redis else None

        # Event queue
        self._event_queue: queue.Queue = queue.Queue(maxsize=100_000)

        # Alert callbacks
        self._alert_callbacks: List[Callable[[AnomalyAlert], None]] = []

        # Threading
        self._running = False
        self._ingest_thread: Optional[threading.Thread] = None
        self._tda_thread: Optional[threading.Thread] = None

        # Metrics
        self._metrics: Dict[str, Any] = {
            "events_processed": 0,
            "tda_ticks": 0,
            "anomalies_detected": 0,
            "last_tick_duration_ms": 0.0,
            "pipeline_uptime_s": 0.0,
        }
        self._start_time: float = 0.0

        # Latest state
        self._latest_diagram: Optional[PersistenceDiagram] = None
        self._latest_alert: Optional[AnomalyAlert] = None
        self._diagram_history: List[PersistenceDiagram] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_alert_callback(self, cb: Callable[[AnomalyAlert], None]):
        """Register a function to be called whenever an alert fires."""
        self._alert_callbacks.append(cb)

    def ingest(self, event: ContainerEvent):
        """Thread-safe: push one container event into the pipeline."""
        try:
            self._event_queue.put_nowait(event)
        except queue.Full:
            logger.warning("Event queue full; dropping event")

    def ingest_batch(self, events: List[ContainerEvent]):
        for e in events:
            self.ingest(e)

    def start(self):
        """Start background processing threads."""
        if self._running:
            return
        self._running = True
        self._start_time = time.time()

        self._ingest_thread = threading.Thread(
            target=self._ingest_loop, daemon=True, name="tda-ingest"
        )
        self._tda_thread = threading.Thread(
            target=self._tda_loop, daemon=True, name="tda-compute"
        )
        self._ingest_thread.start()
        self._tda_thread.start()
        logger.info("StreamingPipeline started")

    def stop(self):
        """Graceful shutdown."""
        self._running = False
        logger.info("StreamingPipeline stopped")

    def process_sync(self, event: ContainerEvent) -> Optional[AnomalyAlert]:
        """
        Synchronous single-step for testing / REST API usage.
        Applies the event and runs TDA immediately.
        """
        self.graph_updater.apply(event)
        return self._run_tda_tick()

    def get_status(self) -> Dict:
        self._metrics["pipeline_uptime_s"] = time.time() - self._start_time
        self._metrics["events_processed"] = self.graph_updater.n_events
        return {
            "metrics": dict(self._metrics),
            "complex": {
                "n_nodes": self.complex.n_nodes,
                "n_edges": self.complex.n_edges,
                "n_simplices": self.complex.n_simplices,
            },
            "latest_alert": self._latest_alert.to_dict()
                            if self._latest_alert else None,
        }

    @property
    def latest_diagram(self) -> Optional[PersistenceDiagram]:
        return self._latest_diagram

    # ------------------------------------------------------------------
    # Internal threads
    # ------------------------------------------------------------------

    def _ingest_loop(self):
        while self._running:
            try:
                event = self._event_queue.get(timeout=1.0)
                self.graph_updater.apply(event)
                if self.redis and self.redis.available:
                    self.redis.upsert_edge(
                        event.origin_facility,
                        event.destination_facility,
                        event.edge_weight,
                    )
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Ingest error: {e}", exc_info=True)

    def _tda_loop(self):
        while self._running:
            tick_start = time.time()
            try:
                alert = self._run_tda_tick()
                if alert:
                    self._latest_alert = alert
                    if alert.is_anomaly:
                        self._metrics["anomalies_detected"] += 1
                        for cb in self._alert_callbacks:
                            try:
                                cb(alert)
                            except Exception as e:
                                logger.error(f"Alert callback error: {e}")
            except Exception as e:
                logger.error(f"TDA tick error: {e}", exc_info=True)

            elapsed = time.time() - tick_start
            self._metrics["last_tick_duration_ms"] = elapsed * 1000
            self._metrics["tda_ticks"] += 1

            # Sleep for remaining tick duration
            sleep_time = max(0, self.tick_seconds - elapsed)
            time.sleep(sleep_time)

    def _run_tda_tick(self) -> Optional[AnomalyAlert]:
        """Run one TDA computation tick."""
        if self.complex.n_nodes < 2:
            return None

        t0 = time.time()
        diagram = self.homology.update(self.complex)
        self._latest_diagram = diagram
        self._diagram_history.append(diagram)

        alert = self.detector.step(diagram, self.complex)
        elapsed = (time.time() - t0) * 1000
        logger.debug(f"TDA tick: {elapsed:.1f}ms | "
                     f"score={alert.anomaly_score:.4f} | "
                     f"anomaly={alert.is_anomaly}")
        return alert
