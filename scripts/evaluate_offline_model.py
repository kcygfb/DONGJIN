from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1] / "python-training-service"
sys.path.insert(0, str(SERVICE_DIR))

from app.grid.online.evaluation import evaluate_offline_model

parser = argparse.ArgumentParser(
    description="独立评估指定模型，不修改在线模型选择。"
)
parser.add_argument("--model-id", required=True)
parser.add_argument("--dataset-id", required=True)
parser.add_argument(
    "--split", choices=["train", "validation", "test"], default="test"
)
args = parser.parse_args()
print(json.dumps(
    evaluate_offline_model(
        args.model_id, args.dataset_id, split=args.split
    ),
    ensure_ascii=False,
    indent=2,
))
