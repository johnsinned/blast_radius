"""Refresh read dependencies from an independent pristine baseline pass."""
import json
from pathlib import Path
import sys
from map_format import decode_mask, compact_masks


def update_reads(result, raw):
    if any(report["outcome"] == "failed" for reports in raw["reports"].values() for report in reports):
        raise ValueError("read collection must pass the pristine baseline")
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

    for path, data in result["files"].items():
        data["reads"] = intern(raw["files"].get(path, []))
    result["dictionaries"] = {site: {"global_whole": data["global_whole"], "global_keys": data["global_keys"], "whole": intern(data["whole"]), "keys": {key: intern(contexts) for key, contexts in data["keys"].items()}} for site, data in raw["dictionary_profile"].items()}
    result["provenance"]["read_validation"] = "independent pristine pass without predicate instrumentation; importer source reads excluded"


if __name__ == "__main__":
    path = Path(sys.argv[1])
    result = json.loads(path.read_text("utf-8"))
    update_reads(result, json.loads(Path(sys.argv[2]).read_text("utf-8")))
    compact_masks(result)
    path.write_text(json.dumps(result, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"updated reads; {path.stat().st_size:,} bytes")
