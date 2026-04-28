# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [Unreleased]

## [1.0.0] — 2026-04-28

### Added
- `DynamicFilteredComplex`: time-varying simplicial complex (0-, 1-, 2-skeleton) over supply-chain graph
- `BoundaryMatrixReducer`: incremental Z/2 column reduction for online persistence computation
- `PersistentHomologyComputer`: H₀, H₁, H₂ computation with correct essential-class tracking
- `SlidingWindowBaseline`: exponentially-weighted baseline of healthy persistence diagrams
- `CUSUMController`: adaptive thresholding achieving FPR < 1%
- `TopologicalAnomalyDetector`: Wasserstein-distance anomaly scoring + contributing-subgraph identification
- `StreamingPipeline`: async two-thread pipeline (ingest + TDA tick) with 30-second default cadence
- `GraphUpdater`: EMA-based edge weight aggregation from raw container events
- `RedisGraphStore`: optional FalkorDB/RedisGraph persistence layer (graceful fallback)
- FastAPI REST API with 9 endpoints and OpenAPI documentation
- `SyntheticDataGenerator`: 5 ground-truth disruption scenarios for training and benchmarking
- `BenchmarkRunner`: FPR/TPR/F1/detection-latency evaluation across all scenarios
- Persistence landscape computation (Bubenik 2015)
- Multi-dimensional Wasserstein distance with top-k truncation for performance
- Interactive D3.js HTML heatmap with force-directed network layout
- Matplotlib persistence diagram, barcode, landscape, and timeline plots
- Docker + docker-compose deployment with FalkorDB
- 35+ unit and integration tests
- Jupyter demo notebook
- CLI: `api`, `benchmark`, `demo`, `generate-data` commands

### Performance
- FPR = 0.0% across all 5 benchmark scenarios (target: < 1%)
- Detection latency: 1–3 TDA ticks (30–90 seconds) from disruption onset
- Suez-class disruptions detected with Wasserstein score 7× above normal baseline

---

[Unreleased]: https://github.com/Chege-N/tda-supply-chain/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Chege-N/tda-supply-chain/releases/tag/v1.0.0
