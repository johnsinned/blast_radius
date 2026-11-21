"""Merge identical type summaries while preserving enum class identities."""
import json
from pathlib import Path
import sys


def compact_types(result):
    summaries, ids, keys = {}, {}, {}
    for data in result["comparisons"].values():
        for pair in data["pairs"]:
            for position in (0, 1):
                previous = pair[position]
                if previous not in ids:
                    summary = dict(result["comparison_types"][previous])
                    summary.pop("instance_eq_check", None)
                    if summary.get("name") != "builtins:NoneType" and summary["kind"] != "canonical_str_enum":
                        summary.pop("name", None)
                    # Two independently defined enum classes may have equal
                    # metadata, but their member identities remain distinct.
                    key = previous if summary["kind"] == "canonical_str_enum" else json.dumps(summary, sort_keys=True)
                    if key not in keys:
                        index = str(len(summaries))
                        keys[key] = index
                        summaries[index] = summary
                    ids[previous] = keys[key]
                pair[position] = ids[previous]
    result["comparison_types"] = summaries


if __name__ == "__main__":
    path = Path(sys.argv[1])
    result = json.loads(path.read_text("utf-8"))
    compact_types(result)
    path.write_text(json.dumps(result, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"compacted type summaries; {len(result['comparison_types'])} summaries; {path.stat().st_size:,} bytes")
