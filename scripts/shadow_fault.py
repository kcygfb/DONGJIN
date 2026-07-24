from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1] / "python-training-service"
sys.path.insert(0, str(SERVICE_DIR))

from app.grid.online.shadow import (
    close_shadow_session,
    create_shadow_session,
    get_shadow_session,
    list_shadow_sessions,
)
from app.grid.scenarios.models import EventType

parser = argparse.ArgumentParser(description="人工管理隔离影子错误会话。")
sub = parser.add_subparsers(dest="command", required=True)
create = sub.add_parser("create")
create.add_argument("--event-type", default="RANDOM", choices=[
    "RANDOM",
    *[item.value for item in EventType if item != EventType.NORMAL],
])
create.add_argument("--target-id")
create.add_argument("--random-seed", type=int, default=20260724)
status = sub.add_parser("status")
status.add_argument("--session-id")
close = sub.add_parser("close")
close.add_argument("--session-id", required=True)
args = parser.parse_args()
if args.command == "create":
    result = create_shadow_session(
        None if args.event_type == "RANDOM" else EventType(args.event_type),
        target_business_id=args.target_id,
        random_seed=args.random_seed,
    )
elif args.command == "status":
    result = (
        get_shadow_session(args.session_id)
        if args.session_id else list_shadow_sessions()
    )
else:
    result = close_shadow_session(args.session_id)
print(json.dumps(result, ensure_ascii=False, indent=2))
