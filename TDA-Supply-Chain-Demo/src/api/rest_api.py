"""
api/rest_api.py
===============
FastAPI REST API for integration with logistics ERP systems.

Endpoints:
  POST /events            - Ingest a container event (or batch)
  GET  /status            - Pipeline health and metrics
  GET  /diagram/latest    - Latest persistence diagram
  GET  /alerts/recent     - Recent anomaly alerts
  GET  /heatmap           - Topological heatmap data for visualization
  POST /simulate          - Run a scenario simulation
  GET  /health            - Liveness probe

Run with:  uvicorn src.api.rest_api:app --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

try:
    from fastapi import FastAPI, HTTPException, BackgroundTasks
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False

from src.streaming.pipeline import StreamingPipeline, ContainerEvent
from src.core.tda_engine import persistence_landscape

logger = logging.getLogger(__name__)

# Global pipeline instance (singleton for this process)
_pipeline: Optional[StreamingPipeline] = None


def get_pipeline() -> StreamingPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = StreamingPipeline(tick_seconds=30)
        _pipeline.start()
        logger.info("StreamingPipeline initialised via API")
    return _pipeline


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

if HAS_FASTAPI:
    class ContainerEventSchema(BaseModel):
        container_id: str
        timestamp: float = Field(default_factory=time.time)
        origin_facility: int
        destination_facility: int
        lat: float
        lon: float
        status: str = "in_transit"
        delay_hours: float = 0.0
        cargo_volume: float = 1.0

    class BatchEventSchema(BaseModel):
        events: List[ContainerEventSchema]

    class SimulationConfig(BaseModel):
        scenario: str = "suez_blockage"  # or "port_congestion", "fraud_ring", "customs_delay"
        n_steps: int = 50
        n_nodes: int = 30
        seed: int = 42

    # ---------------------------------------------------------------------------
    # App factory
    # ---------------------------------------------------------------------------

    def create_app() -> FastAPI:
        app = FastAPI(
            title="TDA Supply Chain Anomaly Detection API",
            description=(
                "Real-time topological anomaly detection for global supply chains "
                "using persistent homology (H₀, H₁, H₂) and Wasserstein distance scoring."
            ),
            version="1.0.0",
            docs_url="/docs",
            redoc_url="/redoc",
        )

        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # ----------------------------------------------------------------
        # Endpoints
        # ----------------------------------------------------------------

        @app.get("/health", tags=["System"])
        def health():
            return {"status": "ok", "timestamp": time.time()}

        @app.get("/status", tags=["System"])
        def status():
            return get_pipeline().get_status()

        @app.post("/events", tags=["Ingestion"])
        def ingest_event(event: ContainerEventSchema):
            """Ingest a single container tracking event."""
            pipe = get_pipeline()
            ce = ContainerEvent(**event.dict())
            alert = pipe.process_sync(ce)
            return {
                "received": True,
                "alert": alert.to_dict() if alert else None,
            }

        @app.post("/events/batch", tags=["Ingestion"])
        def ingest_batch(batch: BatchEventSchema, background_tasks: BackgroundTasks):
            """Ingest a batch of events asynchronously."""
            pipe = get_pipeline()
            events = [ContainerEvent(**e.dict()) for e in batch.events]
            background_tasks.add_task(pipe.ingest_batch, events)
            return {"received": len(events), "queued": True}

        @app.get("/diagram/latest", tags=["TDA"])
        def latest_diagram():
            """Return the most recent persistence diagram."""
            pipe = get_pipeline()
            diag = pipe.latest_diagram
            if not diag:
                raise HTTPException(status_code=404, detail="No diagram computed yet")
            return {
                "timestamp": diag.timestamp,
                "betti_numbers": diag.betti_numbers,
                "pairs": [p.to_dict() for p in diag.pairs
                          if p.persistence is not None and p.persistence > 0.01],
            }

        @app.get("/diagram/landscape", tags=["TDA"])
        def persistence_landscape_endpoint(dim: int = 1, layers: int = 5):
            """Return persistence landscape for visualization."""
            pipe = get_pipeline()
            diag = pipe.latest_diagram
            if not diag:
                raise HTTPException(status_code=404, detail="No diagram computed yet")
            landscape = persistence_landscape(diag, dim=dim, n_layers=layers)
            return {
                "dimension": dim,
                "n_layers": layers,
                "landscape": landscape.tolist(),
            }

        @app.get("/alerts/recent", tags=["Alerts"])
        def recent_alerts(limit: int = 20):
            """Return the most recent anomaly alerts."""
            pipe = get_pipeline()
            alerts = pipe.detector.recent_alerts[-limit:]
            return {
                "count": len(alerts),
                "alerts": [a.to_dict() for a in reversed(alerts)],
            }

        @app.get("/heatmap", tags=["Visualization"])
        def heatmap():
            """
            Return heatmap data: per-node anomaly weight for frontend rendering.
            Higher weight = more likely source of topological anomaly.
            """
            pipe = get_pipeline()
            alert = pipe._latest_alert
            complex_data = pipe.complex

            # Build node weights from edge weights
            node_weights: Dict[int, float] = {}
            for key, simplex in complex_data.simplices.items():
                if len(key) == 2:
                    u, v = key
                    w = simplex.filtration_value
                    node_weights[u] = max(node_weights.get(u, 0), w)
                    node_weights[v] = max(node_weights.get(v, 0), w)

            # Amplify weights for nodes in latest alert
            if alert and alert.is_anomaly:
                for n in alert.contributing_nodes:
                    node_weights[n] = min(1.0, node_weights.get(n, 0) * 2.0)

            return {
                "nodes": [{"id": k, "weight": v} for k, v in node_weights.items()],
                "edges": [
                    {
                        "u": key[0], "v": key[1],
                        "weight": s.filtration_value,
                        "is_anomalous": tuple(sorted(key)) in [
                            tuple(sorted(e)) for e in (alert.contributing_edges if alert else [])
                        ]
                    }
                    for key, s in complex_data.simplices.items() if len(key) == 2
                ],
            }

        @app.post("/simulate", tags=["Simulation"])
        def simulate(config: SimulationConfig):
            """
            Run a simulation scenario and return the anomaly timeline.
            Useful for testing and benchmarking.
            """
            from src.data_gen.synthetic_generator import SyntheticDataGenerator

            gen = SyntheticDataGenerator(
                n_nodes=config.n_nodes,
                seed=config.seed,
            )

            pipe_sim = StreamingPipeline(
                tick_seconds=1e-9,   # Process as fast as possible
                window_size=20,
                warmup_steps=15,
            )

            timeline = []
            for step in range(config.n_steps):
                # Inject disruption halfway through
                disrupted = step > config.n_steps // 2
                events = gen.generate_step(
                    step=step,
                    disrupted=disrupted,
                    scenario=config.scenario,
                )
                for event in events:
                    alert = pipe_sim.process_sync(event)
                    if alert:
                        timeline.append({
                            "step": step,
                            "disrupted": disrupted,
                            **alert.to_dict(),
                        })

            return {
                "scenario": config.scenario,
                "n_steps": config.n_steps,
                "timeline": timeline,
                "summary": {
                    "true_positives": sum(
                        1 for t in timeline
                        if t["disrupted"] and t["is_anomaly"]
                    ),
                    "false_positives": sum(
                        1 for t in timeline
                        if not t["disrupted"] and t["is_anomaly"]
                    ),
                    "n_disrupted_steps": sum(1 for t in timeline if t["disrupted"]),
                },
            }

        return app

    app = create_app()

else:
    # Minimal WSGI fallback without FastAPI
    import http.server
    import json

    class FallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "FastAPI not installed",
                                          "hint": "pip install fastapi uvicorn"}).encode())

    def run_fallback(host="0.0.0.0", port=8001):
        server = http.server.HTTPServer((host, port), FallbackHandler)
        server.serve_forever()

    app = None
