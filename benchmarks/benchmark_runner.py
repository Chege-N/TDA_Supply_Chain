"""
benchmarks/benchmark_runner.py
================================
Benchmark runner that validates the system against known disruptions
(e.g., Suez Canal blockage analog, port congestion, fraud rings).

Metrics:
- Detection time (seconds from disruption onset to first alert)
- False positive rate (must be < 1%)
- True positive rate / recall
- Precision
- F1 score
- Wasserstein distance AUC (area under the anomaly score curve)

Usage:
  python -m benchmarks.benchmark_runner --scenario suez_blockage
  python -m benchmarks.benchmark_runner --all
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

sys.path.insert(0, ".")

from src.streaming.pipeline import StreamingPipeline
from src.data_gen.synthetic_generator import SyntheticDataGenerator
from src.core.anomaly_detector import AnomalyAlert
from src.visualization.visualizer import export_html_heatmap, plot_anomaly_timeline

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    scenario: str
    n_normal_steps: int
    n_disrupted_steps: int
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    detection_time_steps: Optional[int]
    mean_anomaly_score_normal: float
    mean_anomaly_score_disrupted: float
    fpr: float
    tpr: float
    precision: float
    f1: float
    elapsed_wall_seconds: float

    def to_dict(self):
        return asdict(self)

    def print_summary(self):
        print("\n" + "=" * 60)
        print(f"  Benchmark: {self.scenario.upper()}")
        print("=" * 60)
        print(f"  True Positives:    {self.true_positives}")
        print(f"  False Positives:   {self.false_positives}")
        print(f"  True Negatives:    {self.true_negatives}")
        print(f"  False Negatives:   {self.false_negatives}")
        print(f"  TPR (Recall):      {self.tpr:.1%}")
        print(f"  FPR:               {self.fpr:.1%}  (target < 1%)")
        print(f"  Precision:         {self.precision:.1%}")
        print(f"  F1 Score:          {self.f1:.3f}")
        print(f"  Detection latency: {self.detection_time_steps} steps")
        print(f"  Wall time:         {self.elapsed_wall_seconds:.2f}s")
        print(f"  Mean score (normal):    {self.mean_anomaly_score_normal:.4f}")
        print(f"  Mean score (disrupted): {self.mean_anomaly_score_disrupted:.4f}")
        fpr_ok = "✓" if self.fpr < 0.01 else "✗"
        tpr_ok = "✓" if self.tpr > 0.80 else "✗"
        print(f"\n  {fpr_ok} FPR < 1%  |  {tpr_ok} TPR > 80%")
        print("=" * 60 + "\n")


def run_benchmark(scenario: str,
                  n_nodes: int = 40,
                  n_normal: int = 30,
                  n_disrupted: int = 20,
                  n_recovery: int = 10,
                  seed: int = 42,
                  export_html: bool = True) -> BenchmarkResult:
    """Run a single scenario benchmark."""

    logger.info(f"Running benchmark: {scenario}")
    t0 = time.time()

    gen = SyntheticDataGenerator(n_nodes=n_nodes, seed=seed)
    bench_data = gen.generate_benchmark(
        scenario=scenario,
        n_normal=n_normal,
        n_disrupted=n_disrupted,
        n_recovery=n_recovery,
    )

    pipe = StreamingPipeline(tick_seconds=1e-9, window_size=20, warmup_steps=15)

    tp = fp = tn = fn = 0
    detection_step: Optional[int] = None
    scores_normal: List[float] = []
    scores_disrupted: List[float] = []
    all_alerts: List[AnomalyAlert] = []

    for step_data in bench_data:
        step = step_data["step"]
        disrupted = step_data["disrupted"]
        events = step_data["events"]

        last_alert = None
        for event in events:
            last_alert = pipe.process_sync(event)

        if last_alert is None:
            continue

        all_alerts.append(last_alert)
        score = last_alert.anomaly_score
        predicted = last_alert.is_anomaly

        if disrupted:
            scores_disrupted.append(score)
            if predicted:
                tp += 1
                if detection_step is None:
                    detection_step = step - n_normal  # steps into disruption
            else:
                fn += 1
        else:
            scores_normal.append(score)
            if predicted:
                fp += 1
            else:
                tn += 1

    elapsed = time.time() - t0
    n_neg = tn + fp
    n_pos = tp + fn
    fpr = fp / n_neg if n_neg > 0 else 0.0
    tpr = tp / n_pos if n_pos > 0 else 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    f1 = (2 * precision * tpr / (precision + tpr)) if (precision + tpr) > 0 else 0.0

    result = BenchmarkResult(
        scenario=scenario,
        n_normal_steps=n_normal,
        n_disrupted_steps=n_disrupted,
        true_positives=tp,
        false_positives=fp,
        true_negatives=tn,
        false_negatives=fn,
        detection_time_steps=detection_step,
        mean_anomaly_score_normal=float(sum(scores_normal) / len(scores_normal))
                                  if scores_normal else 0.0,
        mean_anomaly_score_disrupted=float(sum(scores_disrupted) / len(scores_disrupted))
                                     if scores_disrupted else 0.0,
        fpr=fpr,
        tpr=tpr,
        precision=precision,
        f1=f1,
        elapsed_wall_seconds=elapsed,
    )

    if export_html and all_alerts:
        try:
            from src.visualization.visualizer import plot_anomaly_timeline
            fig = plot_anomaly_timeline(all_alerts, save_path=f"benchmark_{scenario}_timeline.png")
            export_html_heatmap(
                pipe.complex, pipe._latest_alert, pipe.latest_diagram,
                output_path=f"benchmark_{scenario}_heatmap.html",
            )
        except Exception as e:
            logger.warning(f"Could not export visuals: {e}")

    return result


def run_all_benchmarks(scenarios=None) -> List[BenchmarkResult]:
    if scenarios is None:
        scenarios = [
            "suez_blockage",
            "port_congestion",
            "fraud_ring",
            "customs_delay",
            "geopolitical_shock",
        ]

    results = []
    for sc in scenarios:
        try:
            r = run_benchmark(sc)
            r.print_summary()
            results.append(r)
        except Exception as e:
            logger.error(f"Benchmark {sc} failed: {e}", exc_info=True)

    # Summary table
    print("\n" + "=" * 80)
    print("  AGGREGATE BENCHMARK SUMMARY")
    print("=" * 80)
    print(f"{'Scenario':<25} {'TPR':>8} {'FPR':>8} {'F1':>8} {'Det.Steps':>12}")
    print("-" * 80)
    for r in results:
        det = str(r.detection_time_steps) if r.detection_time_steps else "N/A"
        fpr_flag = "✓" if r.fpr < 0.01 else "✗"
        print(f"{r.scenario:<25} {r.tpr:>7.1%} {r.fpr:>7.1%}{fpr_flag} "
              f"{r.f1:>8.3f} {det:>12}")
    print("=" * 80)

    with open("benchmark_results.json", "w") as f:
        json.dump([r.to_dict() for r in results], f, indent=2)
    logger.info("Results saved to benchmark_results.json")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TDA Supply Chain Benchmark Runner")
    parser.add_argument("--scenario", type=str, default=None,
                        help="Specific scenario to run")
    parser.add_argument("--all", action="store_true", help="Run all scenarios")
    parser.add_argument("--nodes", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-html", action="store_true")
    args = parser.parse_args()

    if args.all or not args.scenario:
        run_all_benchmarks()
    else:
        r = run_benchmark(args.scenario, n_nodes=args.nodes, seed=args.seed,
                          export_html=not args.no_html)
        r.print_summary()
