#!/usr/bin/env python3
"""
main.py
=======
CLI entry point for the TDA Supply Chain Anomaly Detection system.

Usage:
  python main.py api              - Start the REST API server
  python main.py benchmark        - Run all benchmarks
  python main.py demo             - Run a quick demo simulation
  python main.py generate-data    - Generate and export synthetic CSV data
"""

import argparse
import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("tda_supply_chain.log"),
    ],
)
logger = logging.getLogger(__name__)


def cmd_api(args):
    """Start the FastAPI REST server."""
    try:
        import uvicorn
    except ImportError:
        print("ERROR: uvicorn not installed. Run: pip install uvicorn[standard]")
        sys.exit(1)

    from src.api.rest_api import app
    logger.info(f"Starting API server on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def cmd_benchmark(args):
    """Run benchmarks."""
    from benchmarks.benchmark_runner import run_all_benchmarks, run_benchmark
    if args.scenario:
        r = run_benchmark(args.scenario, n_nodes=args.nodes, seed=args.seed)
        r.print_summary()
    else:
        run_all_benchmarks()


def cmd_demo(args):
    """Run a quick interactive demo."""
    from src.streaming.pipeline import StreamingPipeline
    from src.data_gen.synthetic_generator import SyntheticDataGenerator
    from src.visualization.visualizer import ascii_heatmap, export_html_heatmap
    import os

    print("\n" + "="*60)
    print("  TDA Supply Chain Anomaly Detection — DEMO")
    print("="*60 + "\n")

    gen = SyntheticDataGenerator(n_nodes=args.nodes, seed=args.seed)
    pipe = StreamingPipeline(tick_seconds=1e-9, window_size=20, warmup_steps=15)

    n_total = 60
    disruption_start = 30

    print(f"Simulating {n_total} time steps ({disruption_start} normal, "
          f"{n_total - disruption_start} with '{args.scenario}' disruption)...\n")

    for step in range(n_total):
        disrupted = step >= disruption_start
        events = gen.generate_step(step, disrupted=disrupted, scenario=args.scenario)

        last_alert = None
        for e in events:
            last_alert = pipe.process_sync(e)

        phase = "DISRUPTION" if disrupted else "normal   "
        status_icon = "🔴" if (last_alert and last_alert.is_anomaly) else "🟢"
        score = last_alert.anomaly_score if last_alert else 0.0
        print(f"Step {step:3d} [{phase}] {status_icon} score={score:.4f} "
              f"nodes={pipe.complex.n_nodes} edges={pipe.complex.n_edges}",
              flush=True)
        time.sleep(0.05)

    # Final outputs
    print("\n" + "-"*60)
    print(ascii_heatmap(pipe.complex, pipe._latest_alert))

    out_path = "demo_heatmap.html"
    export_html_heatmap(pipe.complex, pipe._latest_alert, pipe.latest_diagram, out_path)
    print(f"\n✓ Interactive heatmap saved: {os.path.abspath(out_path)}")

    status = pipe.get_status()
    print(f"\nPipeline summary:")
    print(f"  Events processed:   {status['metrics']['events_processed']}")
    print(f"  TDA ticks:          {status['metrics']['tda_ticks']}")
    print(f"  Anomalies detected: {status['metrics']['anomalies_detected']}")


def cmd_generate_data(args):
    """Generate and export synthetic data as CSV."""
    from src.data_gen.synthetic_generator import SyntheticDataGenerator

    gen = SyntheticDataGenerator(n_nodes=args.nodes, seed=args.seed)
    bench = gen.generate_benchmark(
        scenario=args.scenario,
        n_normal=args.n_normal,
        n_disrupted=args.n_disrupted,
    )
    out = args.output
    n_rows = gen.export_csv(bench, out)
    print(f"✓ Exported {n_rows} rows to {out}")


def main():
    parser = argparse.ArgumentParser(
        description="TDA Supply Chain Anomaly Detection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    # api
    api_p = sub.add_parser("api", help="Start REST API server")
    api_p.add_argument("--host", default="0.0.0.0")
    api_p.add_argument("--port", type=int, default=8000)

    # benchmark
    bench_p = sub.add_parser("benchmark", help="Run benchmarks")
    bench_p.add_argument("--scenario", default=None,
                         choices=["suez_blockage", "port_congestion",
                                  "fraud_ring", "customs_delay", "geopolitical_shock"])
    bench_p.add_argument("--nodes", type=int, default=40)
    bench_p.add_argument("--seed", type=int, default=42)

    # demo
    demo_p = sub.add_parser("demo", help="Run quick demo simulation")
    demo_p.add_argument("--scenario", default="suez_blockage")
    demo_p.add_argument("--nodes", type=int, default=30)
    demo_p.add_argument("--seed", type=int, default=42)

    # generate-data
    gen_p = sub.add_parser("generate-data", help="Export synthetic CSV data")
    gen_p.add_argument("--scenario", default="suez_blockage")
    gen_p.add_argument("--nodes", type=int, default=50)
    gen_p.add_argument("--n-normal", type=int, default=50)
    gen_p.add_argument("--n-disrupted", type=int, default=30)
    gen_p.add_argument("--output", default="synthetic_data.csv")
    gen_p.add_argument("--seed", type=int, default=42)

    args = parser.parse_args()

    if args.command == "api":
        cmd_api(args)
    elif args.command == "benchmark":
        cmd_benchmark(args)
    elif args.command == "demo":
        cmd_demo(args)
    elif args.command == "generate-data":
        cmd_generate_data(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
