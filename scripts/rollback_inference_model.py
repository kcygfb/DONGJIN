from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1] / "python-training-service"
sys.path.insert(0, str(SERVICE_DIR))

from app.grid.online.model_manager import rollback_inference_model

parser = argparse.ArgumentParser(description="人工回滚到上一次模型选择。")
parser.add_argument("--actor", default="powershell-user")
args = parser.parse_args()
print(json.dumps(
    rollback_inference_model(actor=args.actor),
    ensure_ascii=False,
    indent=2,
))
