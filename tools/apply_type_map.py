"""Refresh type-state observations while retaining bounded primitive fixtures."""
import json
from pathlib import Path
import sys
from map_format import decode_mask, compact_masks


def update_types(result, raw):
    if any(r["outcome"] == "failed" for reports in raw["reports"].values() for r in reports):
        raise ValueError("type collection must pass the pristine baseline")
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

    eligible = decode_mask(result["sets"][result["comparison_profiled_tests"]])
    completed = set(raw["executed"]) - set(raw["reports"])
    eligible &= decode_mask(result["sets"][intern(completed)])
    result["comparison_profiled_tests"] = intern(name for name, i in numbers.items() if eligible & (1 << i))
    profile = raw["comparison_profile"]
    result["comparison_types"].update({"linux_none:" + key: value for key, value in profile["types"].items()})
    for site, current in profile["sites"].items():
        data = result["comparisons"].setdefault(site, {"global": False})
        data["global"] = data["global"] or current["global"]
        data["pairs"] = [["linux_none:" + a, "linux_none:" + b, intern(contexts)] for a, b, contexts in current["pairs"]]
    result["provenance"]["type_validation"] = "independent pristine Linux pass; original equality-method AST and live type-state guards; no changed operation evaluated"
    compact_masks(result)


if __name__ == "__main__":
    path = Path(sys.argv[1])
    result = json.loads(path.read_text("utf-8"))
    update_types(result, json.loads(Path(sys.argv[2]).read_text("utf-8")))
    path.write_text(json.dumps(result, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"updated type states; {path.stat().st_size:,} bytes")
