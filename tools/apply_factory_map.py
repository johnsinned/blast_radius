"""Compile original factory-closure invocation dependencies."""
import json
from pathlib import Path
import sys
from map_format import decode_mask, compact_masks


def update_factories(result, raw):
    profile = raw["factory_profile"]
    if not profile["valid"] or any(r["outcome"] == "failed" for reports in raw["reports"].values() for r in reports):
        raise ValueError("factory collection must pass without tracking errors")
    numbers = {name: i for i, name in enumerate(result["tests"])}
    sets = {format(decode_mask(value), "x"): i for i, value in enumerate(result["sets"])}

    def intern(contexts):
        bits = 0
        for context in contexts:
            for name in raw["setup"].get(context, [context]):
                if name in numbers: bits |= 1 << numbers[name]
        value = format(bits, "x")
        if value not in sets:
            sets[value] = len(result["sets"])
            result["sets"].append(value)
        return sets[value]

    result["factory_profiled_tests"] = intern(set(raw["executed"]) - set(raw["reports"]))
    result["factories"] = {site: {**data, "users": intern(data["users"])} for site, data in profile["sites"].items()}
    result["provenance"]["factory_validation"] = "pristine returned-closure calls; factories restricted to nested function definition plus return, with input-independent function metadata and protected source spans"
    compact_masks(result)


if __name__ == "__main__":
    path = Path(sys.argv[1])
    result = json.loads(path.read_text("utf-8"))
    update_factories(result, json.loads(Path(sys.argv[2]).read_text("utf-8")))
    path.write_text(json.dumps(result, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"attached factory dependencies; {path.stat().st_size:,} bytes")
