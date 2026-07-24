from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1] / "python-training-service"
sys.path.insert(0, str(SERVICE_DIR))

from app.grid.datasets.builder import DatasetBuildRequest, build_dataset
from app.grid.scenarios.models import EventType, ScenarioBatchRequest
from app.grid.scenarios.service import generate_scenario_batch


def main() -> None:
    parser = argparse.ArgumentParser(
        description="只生成白箱场景和GNN数据集，不启动训练。"
    )
    parser.add_argument("--samples-per-type", type=int, default=8)
    parser.add_argument("--random-seed", type=int, default=20260724)
    parser.add_argument("--batch-id")
    parser.add_argument("--dataset-id")
    parser.add_argument(
        "--event-types",
        nargs="+",
        choices=[item.value for item in EventType],
        default=[item.value for item in EventType],
    )
    args = parser.parse_args()
    batch = generate_scenario_batch(
        ScenarioBatchRequest(
            batchId=args.batch_id,
            samplesPerType=args.samples_per_type,
            randomSeed=args.random_seed,
            eventTypes=[EventType(item) for item in args.event_types],
        )
    )
    dataset = build_dataset(
        DatasetBuildRequest(
            batchId=batch["batchId"],
            datasetId=args.dataset_id,
            randomSeed=args.random_seed,
        )
    )
    print(json.dumps(
        {
            "status": "COMPLETED",
            "nextAction": "如需训练，请另行执行train_offline_model.py。",
            "scenarioBatch": batch,
            "dataset": dataset,
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
