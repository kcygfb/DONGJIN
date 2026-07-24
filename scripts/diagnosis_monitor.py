from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request


def request(path: str, method: str) -> dict:
    value = urllib.request.Request(
        "http://127.0.0.1:8001" + path,
        data=b"{}" if method == "POST" else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(value, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise SystemExit(
            "无法连接Python服务，请先启动uvicorn；周期研判由服务进程持有。"
        ) from exc


parser = argparse.ArgumentParser(
    description="显式启动、查看或停止服务内周期研判。"
)
parser.add_argument("command", choices=["start", "status", "stop"])
args = parser.parse_args()
method = "GET" if args.command == "status" else "POST"
print(json.dumps(
    request(f"/diagnosis/monitor/{args.command}", method),
    ensure_ascii=False,
    indent=2,
))
