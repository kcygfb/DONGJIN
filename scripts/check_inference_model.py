from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1] / "python-training-service"
sys.path.insert(0, str(SERVICE_DIR))

from app.grid.online.model_manager import (
    check_model_compatibility,
    get_selected_model,
)

parser = argparse.ArgumentParser(description="检查模型与当前P1兼容性。")
parser.add_argument("--model-id")
args = parser.parse_args()
model_id = args.model_id
if not model_id:
    current = get_selected_model(required=True)
    model_id = current["modelId"]
print(json.dumps(
    check_model_compatibility(model_id),
    ensure_ascii=False,
    indent=2,
))
