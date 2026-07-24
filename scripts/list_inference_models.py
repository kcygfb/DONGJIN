from __future__ import annotations

import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1] / "python-training-service"
sys.path.insert(0, str(SERVICE_DIR))

from app.grid.online.model_manager import list_inference_models

print(json.dumps(list_inference_models(), ensure_ascii=False, indent=2))
