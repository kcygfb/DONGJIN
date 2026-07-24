from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1] / "python-training-service"
sys.path.insert(0, str(SERVICE_DIR))

from app.grid.online.diagnosis import diagnose_snapshot
from app.grid.online.shadow import (
    diagnose_shadow_session,
    reveal_shadow_session,
)
from app.grid.simulation.engine import get_simulation_engine

parser = argparse.ArgumentParser(description="人工执行一次正式或影子研判。")
sub = parser.add_subparsers(dest="command", required=True)
sub.add_parser("current")
shadow = sub.add_parser("shadow")
shadow.add_argument("--session-id", required=True)
reveal = sub.add_parser("reveal")
reveal.add_argument("--session-id", required=True)
args = parser.parse_args()
if args.command == "current":
    snapshot = get_simulation_engine().current_snapshot()
    if snapshot is None:
        raise SystemExit("Redis中尚无当前动态快照")
    result = diagnose_snapshot(snapshot)
elif args.command == "shadow":
    result = diagnose_shadow_session(args.session_id)
else:
    result = reveal_shadow_session(args.session_id)
print(json.dumps(result, ensure_ascii=False, indent=2))
