"""Reproduce the unmodified starter without requiring Docker."""
import json
from pathlib import Path
import sys

data = Path(sys.argv[1] if len(sys.argv) > 1 else "sample")
tests = json.loads((data / "tests.json").read_text())["tests"]
workers = [[], [], []]
loads = [0, 0, 0]
for test in sorted(tests, key=lambda x: x["seconds"], reverse=True):
    i = loads.index(min(loads))
    workers[i].append(test["number"])
    loads[i] += round(test["seconds"] * 1000)
for patch in sorted((data / "commits").glob("*.patch")):
    print(json.dumps({"commit": patch.stem, "workers": workers}))
