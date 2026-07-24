from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1] / "python-training-service"
sys.path.insert(0, str(SERVICE_DIR))

from app.grid.online.short_circuit import run_short_circuit_analysis

parser = argparse.ArgumentParser(description="人工执行独立短路分析。")
parser.add_argument("run", nargs="?")
parser.add_argument("--target-id", required=True)
parser.add_argument(
    "--fault-type", choices=["3ph", "2ph", "1ph"], default="3ph"
)
parser.add_argument("--case", choices=["max", "min"], default="max")
parser.add_argument("--r-fault-ohm", type=float, default=0.0)
parser.add_argument("--x-fault-ohm", type=float, default=0.0)
parser.add_argument("--s-sc-mva", type=float, required=True)
parser.add_argument("--rx", type=float, required=True)
args = parser.parse_args()
print(json.dumps(
    run_short_circuit_analysis(
        args.target_id,
        fault_type=args.fault_type,
        case=args.case,
        r_fault_ohm=args.r_fault_ohm,
        x_fault_ohm=args.x_fault_ohm,
        s_sc_mva=args.s_sc_mva,
        rx=args.rx,
    ),
    ensure_ascii=False,
    indent=2,
))
