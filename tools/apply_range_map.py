"""Compile enclosing baseline integer ranges, grouped by equal bounds."""
import json
from pathlib import Path
import sys
from map_format import decode_mask, compact_masks


def update_ranges(result, raw):
    if any(r["outcome"] == "failed" for reports in raw["reports"].values() for r in reports):
        raise ValueError("range collection must pass the pristine baseline")
    numbers = {name: i for i, name in enumerate(result["tests"])}
    sets = {format(decode_mask(value), "x"): i for i, value in enumerate(result["sets"])}

    def intern(contexts):
        bits = 0
        for context in contexts:
            for name in raw["setup"].get(context, [context]):
                if name in numbers:
                    bits |= 1 << numbers[name]
        value = format(bits, "x")
        if value not in sets:
            sets[value] = len(result["sets"])
            result["sets"].append(value)
        return sets[value]

    result["integer_profiled_tests"] = intern(set(raw["executed"]) - set(raw["reports"]))
    result["integer_ranges"] = {}
    for site, data in raw["comparison_profile"].get("integer_ranges", {}).items():
        groups = {}
        for context, bounds in data["ranges"].items():
            groups.setdefault(tuple(bounds), []).append(context)
        result["integer_ranges"][site] = {"global": data["global"], "unknown": intern(data["unknown"]), "groups": [[*bounds, intern(contexts)] for bounds, contexts in groups.items()]}
    result["provenance"]["integer_validation"] = "pristine integer operand min/max per test; enclosing intervals include unobserved integers; changed predicates evaluated only at runtime"
    compact_masks(result)


if __name__ == "__main__":
    path = Path(sys.argv[1])
    result = json.loads(path.read_text("utf-8"))
    update_ranges(result, json.loads(Path(sys.argv[2]).read_text("utf-8")))
    path.write_text(json.dumps(result, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"updated integer ranges; {path.stat().st_size:,} bytes")
