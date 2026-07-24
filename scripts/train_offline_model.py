from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1] / "python-training-service"
sys.path.insert(0, str(SERVICE_DIR))

from app.grid.training.trainer import (
    OfflineTrainingRequest,
    train_offline_model,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="只训练指定数据集，不自动选择在线模型。"
    )
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--model-id")
    parser.add_argument("--random-seed", type=int, default=20260724)
    parser.add_argument("--maximum-epochs", type=int, default=120)
    args = parser.parse_args()
    model = train_offline_model(
        OfflineTrainingRequest(
            datasetId=args.dataset_id,
            modelId=args.model_id,
            randomSeed=args.random_seed,
            maximumEpochs=args.maximum_epochs,
        )
    )
    print(json.dumps(
        {
            "status": "COMPLETED",
            "nextAction": (
                "训练完成但未选择在线模型；请检查指标后另行执行"
                "select_inference_model.py。"
            ),
            "model": model,
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
