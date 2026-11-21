"""Merge independently measured baseline subsets by pytest node id."""
import json
from pathlib import Path
import sys

result = {"tests": [], "lines": {}, "files": {}, "classes": {}, "setup": {}, "reports": {}}
for name in sys.argv[2:]:
    raw = json.loads(Path(name).read_text("utf-8"))
    result["tests"] = sorted(set(result["tests"]) | set(raw["tests"]))
    for path, lines in raw["lines"].items():
        target = result["lines"].setdefault(path, {})
        for n, contexts in lines.items():
            target[n] = sorted(set(target.get(n, [])) | set(contexts))
    for key in ("files", "classes", "setup"):
        for name, contexts in raw[key].items():
            result[key][name] = sorted(set(result[key].get(name, [])) | set(contexts))
    for name, reports in raw["reports"].items():
        result["reports"].setdefault(name, []).extend(reports)
Path(sys.argv[1]).write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
print(f"merged {len(result['tests'])} baseline test ids")
