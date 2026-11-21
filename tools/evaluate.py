"""Local benchmark; only this development tool reads sample answers."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from score import evaluate_run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=ROOT / "sample")
    parser.add_argument("--source", type=Path, default=ROOT / ".work/sqlglot")
    parser.add_argument("--output", type=Path, default=ROOT / "output.jsonl")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--map", type=Path, help="evaluate an alternative baseline map")
    args = parser.parse_args()
    command = [sys.executable, str(ROOT / "selector.py"), "--data", str(args.data), "--source", str(args.source)]
    if args.verbose:
        command.append("--verbose")
    if args.map:
        command.extend(["--map", str(args.map)])
    start = time.perf_counter()
    run = subprocess.run(command, stdout=subprocess.PIPE, check=True)
    elapsed = time.perf_counter() - start
    args.output.write_bytes(run.stdout)
    tests = json.loads((args.data / "tests.json").read_text("utf-8"))["tests"]
    answers = json.loads((args.data / "answers.json").read_text("utf-8"))["commits"]
    result = evaluate_run(run.stdout, tests, answers)
    print(json.dumps({**result, "wall_seconds": round(elapsed, 3), "stdout_bytes": len(run.stdout)}, indent=2))
    if not result["ok"] or result["missed_commits"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
