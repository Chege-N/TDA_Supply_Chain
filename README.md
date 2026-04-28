# TDA Supply Chain Anomaly Detection

> **Real-time topological anomaly detection for global supply chains using persistent homology.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-green.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Problem Statement

Global supply chains are vulnerable to disruptions—port congestion, customs delays, fraud, geopolitical shocks. Existing methods rely on threshold-based or simple statistical models that **fail to capture the complex, high-dimensional topology** of shipment networks.

This system uses **Persistent Homology** from Topological Data Analysis (TDA) to detect anomalies that are invisible to conventional monitoring.

---

## Mathematical Foundation

### Dynamic Filtered Simplicial Complex

Given streaming GPS-like tracking data from containers, we construct a **time-varying simplicial complex**:

```
K(t) = {σ : σ is a facility node, edge, or triangle with filtration value ≤ t}
```

- **Nodes (0-simplices)**: Facilities — ports, warehouses, customs hubs
- **Edges (1-simplices)**: Cargo flow routes, weighted by congestion/delay
- **Triangles (2-simplices)**: Closed route triangles (auto-generated)

Edge filtration weight is computed as:
```
w(u,v) = EMA_α( delay_hours/24 + status_penalty )
```

### Persistent Homology

For each time step, we compute **H₀, H₁, H₂**:

| Dimension | Topological Feature | Supply Chain Meaning |
|-----------|--------------------|-----------------------|
| H₀ | Connected components | Network fragmentation |
| H₁ | 1-cycles / loops | Broken routing loops, fraud cycles |
| H₂ | 2-voids | Large-scale regional failures |

The standard persistence algorithm (Simon's Z/2 column reduction) produces a **persistence barcode** — intervals `[birth, death)` representing feature lifetimes.

### Topological Anomaly Score

```
score(t) = Σ_d  w_d · W₂( Dgm_d(t),  mean_window_Dgm_d )
```

Where `W₂` is the 2-Wasserstein distance between the current persistence diagram and a sliding window baseline of healthy diagrams.

### CUSUM Adaptive Thresholding

To achieve **FPR < 1%**, we use a CUSUM control chart:
```
C⁺ₜ = max(0, C⁺ₜ₋₁ + zₜ − k)
C⁻ₜ = max(0, C⁻ₜ₋₁ − zₜ − k)
alarm if max(C⁺ₜ, C⁻ₜ) > h
```

---

## Architecture

```
Container GPS Events
         │
         ▼
  ┌─────────────┐
  │ Event Queue  │  (async, 100k buffer)
  └──────┬───────┘
         │
         ▼
  ┌─────────────────┐
  │  Graph Updater   │  EMA edge weights
  │  (RedisGraph opt)│
  └──────┬───────────┘
         │
         ▼
  ┌────────────────────────┐
  │  Dynamic Simplicial    │  0-, 1-, 2-skeleton
  │  Complex               │  Auto triangle closure
  └──────┬─────────────────┘
         │  (every 30s)
         ▼
  ┌──────────────────────────┐
  │  Persistent Homology     │  H₀, H₁, H₂
  │  Computer                │  Incremental Z/2 reduction
  └──────┬───────────────────┘
         │
         ▼
  ┌───────────────────────────┐
  │  Topological Anomaly      │  Wasserstein distance
  │  Detector                 │  CUSUM + sliding window
  └──────┬────────────────────┘
         │
         ▼
  ┌──────────────────┐    ┌─────────────────────┐
  │  Alert Bus       │───▶│  REST API (FastAPI)  │
  │  (callbacks)     │    │  ERP Integration     │
  └──────────────────┘    └─────────────────────┘
         │
         ▼
  ┌─────────────────────────┐
  │  HTML Heatmap           │
  │  + Persistence Diagrams │
  │  + Anomaly Timeline     │
  └─────────────────────────┘
```

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/your-org/tda-supply-chain.git
cd tda-supply-chain
pip install -r requirements.txt
```

### 2. Run the Demo

```bash
python main.py demo --scenario suez_blockage --nodes 30
```

Output: step-by-step console output + `demo_heatmap.html` (open in browser).

### 3. Start the REST API

```bash
python main.py api --port 8000
# or with Docker:
docker-compose up
```

API docs: http://localhost:8000/docs

### 4. Run Benchmarks

```bash
python main.py benchmark --all
# or a specific scenario:
python main.py benchmark --scenario fraud_ring
```

### 5. Generate Synthetic Data

```bash
python main.py generate-data --scenario customs_delay --n-normal 100 --n-disrupted 50
```

---

## REST API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/health` | Liveness probe |
| `GET`  | `/status` | Pipeline metrics + graph stats |
| `POST` | `/events` | Ingest single container event |
| `POST` | `/events/batch` | Ingest batch (async) |
| `GET`  | `/diagram/latest` | Latest persistence diagram |
| `GET`  | `/diagram/landscape` | Persistence landscape |
| `GET`  | `/alerts/recent` | Last N anomaly alerts |
| `GET`  | `/heatmap` | Node/edge weights for visualization |
| `POST` | `/simulate` | Run a scenario simulation |

### Example: Ingest an Event

```bash
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{
    "container_id": "MSCU1234567",
    "timestamp": 1700000000,
    "origin_facility": 42,
    "destination_facility": 7,
    "lat": 31.2,
    "lon": 121.4,
    "status": "delayed",
    "delay_hours": 36.0,
    "cargo_volume": 0.85
  }'
```

### Example: Run Suez Simulation

```bash
curl -X POST http://localhost:8000/simulate \
  -H "Content-Type: application/json" \
  -d '{"scenario": "suez_blockage", "n_steps": 60, "n_nodes": 40}'
```

---

## Disruption Scenarios

| Scenario | Description | Expected Topology |
|----------|-------------|-------------------|
| `suez_blockage` | Canal blockage, global rerouting | H₁↑ H₀↑ |
| `port_congestion` | Weather-driven cluster congestion | H₁↔ H₀↔ |
| `fraud_ring` | Phantom shipments — artificial 1-cycles | H₁↑↑ |
| `customs_delay` | Inspection bottleneck at hub | Edge weight spike |
| `geopolitical_shock` | Trade route closure cascade | H₀↑↑ |

---

## Topological Signatures

```
Normal operation:
  Barcode:  ──────   (short bars, H1 features die quickly)
  Score:    0.02–0.08

Suez blockage:
  Barcode:  ──────────────────────  (long-lived H1 bars from new cycles)
  Score:    0.35–0.90
  Alert:    "H₁ (routing loops) increased by 4; contributing edges: [(7,12),(12,31),...]"

Fraud ring:
  Barcode:  Persistent 1-cycles with ~0 birth (phantom shipments have no delay)
  Score:    0.20–0.50
  Note:     Detected even without delay anomalies — purely topological signal!
```

---

## Performance Targets

| Metric | Target | Implementation |
|--------|--------|----------------|
| Detection latency | < 5 minutes | 30s tick × 10 ticks |
| False positive rate | < 1% | CUSUM + 2.5σ threshold |
| True positive rate | > 80% | Benchmarked on 5 scenarios |
| Events/sec throughput | > 10,000 | Async queue + EMA |
| Complex size | Up to 1000 nodes | Incremental Z/2 reduction |

---

## Project Structure

```
tda_supply_chain/
├── src/
│   ├── core/
│   │   ├── tda_engine.py          # Simplicial complex + persistent homology
│   │   └── anomaly_detector.py    # Wasserstein scoring + CUSUM
│   ├── streaming/
│   │   └── pipeline.py            # Real-time streaming pipeline
│   ├── api/
│   │   └── rest_api.py            # FastAPI REST endpoints
│   ├── data_gen/
│   │   └── synthetic_generator.py # Synthetic benchmark data
│   └── visualization/
│       └── visualizer.py          # Plots + HTML heatmap
├── tests/
│   └── test_tda_engine.py         # 35+ unit + integration tests
├── benchmarks/
│   └── benchmark_runner.py        # Full benchmark suite
├── notebooks/
│   └── demo_notebook.ipynb        # Interactive Jupyter demo
├── config/
│   └── settings.yaml              # Configuration
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── main.py                        # CLI entry point
└── README.md
```

---

## Extension Points

### Using Dionysus2 (C++ acceleration)

```python
# In tda_engine.py, replace BoundaryMatrixReducer with:
import dionysus as d
f = d.Filtration(simplices)
p = d.homology_persistence(f)
dgms = d.init_diagrams(p, f)
```

### Using ripser (GPU-accelerated)

```python
from ripser import ripser
result = ripser(point_cloud, maxdim=2)
```

### ERP Integration

```python
import requests

# Register a webhook callback
def on_alert(alert):
    requests.post("https://your-erp.com/webhooks/supply-chain-alert",
                  json=alert.to_dict())

pipeline.register_alert_callback(on_alert)
```

---

## Mathematical References

1. Edelsbrunner, H., Letscher, D., Zomorodian, A. (2002). *Topological Persistence and Simplification.* Discrete & Computational Geometry.
2. Carlsson, G. (2009). *Topology and Data.* Bulletin of the AMS.
3. Cohen-Steiner, D., Edelsbrunner, H., Harer, J. (2007). *Stability of Persistence Diagrams.* Discrete & Computational Geometry.
4. Bubenik, P. (2015). *Statistical Topological Data Analysis using Persistence Landscapes.* JMLR.
5. Page, E.S. (1954). *Continuous Inspection Schemes.* Biometrika (CUSUM).

---

## License

MIT License. See [LICENSE](LICENSE).

---

## Impact

- **Pre-emptive rerouting**: Detect H₁ changes (new routing cycles from blockages) up to 5 minutes before conventional delay-based alerts.
- **Fraud detection**: Phantom shipments create persistent 1-cycles with low filtration weight — invisible to threshold-based systems.
- **Economic value**: Even a 1-hour earlier detection of a Suez-class event reduces losses by tens of millions of dollars.
