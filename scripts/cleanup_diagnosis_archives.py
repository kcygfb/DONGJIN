from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1] / "python-training-service"
sys.path.insert(0, str(SERVICE_DIR))

from app.grid.settings import get_grid_settings

parser = argparse.ArgumentParser(
    description="人工预览或清理过期研判档案；默认只预览。"
)
parser.add_argument("--older-than-days", type=int, default=30)
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()
if args.older_than_days < 1:
    raise SystemExit("older-than-days必须大于等于1")

root = get_grid_settings().resolved_diagnosis_dir
cutoff = datetime.now(UTC) - timedelta(days=args.older_than_days)
candidates = []
if root.is_dir():
    for path in root.iterdir():
        if not path.is_dir() or not path.name.startswith("diagnosis-"):
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if modified < cutoff:
            candidates.append(str(path))
            if args.apply:
                shutil.rmtree(path)
print(json.dumps(
    {
        "mode": "APPLY" if args.apply else "DRY_RUN",
        "olderThanDays": args.older_than_days,
        "count": len(candidates),
        "paths": candidates,
        "message": (
            "已删除列出的研判档案。"
            if args.apply
            else "仅预览；确认后增加--apply才会删除。"
        ),
    },
    ensure_ascii=False,
    indent=2,
))
