"""Attach original validator inputs and dictionary consumers to the map."""
import json
from pathlib import Path
import sys
try:
    from .map_format import decode_mask, compact_masks
except ImportError:
    from map_format import decode_mask, compact_masks


def update_requirements(result, raw):
    if len(set(raw["executed"])) < 1600 or any(r["outcome"] == "failed" for reports in raw["reports"].values() for r in reports):
        raise ValueError("required-argument collection must pass the full pristine suite")
    profile = raw["requirement_profile"]
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

    result["requirement_profiled_tests"] = intern(set(raw["executed"]) - set(raw["reports"]))
    result["requirements"] = {"proof": profile["proof"], "unknown": intern(profile["unknown"]), "sites": {site: {**data, "raw": intern(data["raw"]), "keys": {key: intern(contexts) for key, contexts in data["keys"].items()}} for site, data in profile["sites"].items() if site.startswith("sqlglot/") and site in raw["dictionary_profile"]}}
    result["provenance"]["requirement_validation"] = "pristine validator inputs, inherited dictionary identities, and independent value consumers; missing/unknown arguments and multiple-error ordering retained; dictionary copies/spreads disable origin refinement"
    compact_masks(result)


if __name__ == "__main__":
    path = Path(sys.argv[1])
    result = json.loads(path.read_text("utf-8"))
    update_requirements(result, json.loads(Path(sys.argv[2]).read_text("utf-8")))
    path.write_text(json.dumps(result, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"attached required-argument dependencies; {path.stat().st_size:,} bytes")
