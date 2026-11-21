"""Attach baseline operand-type profiles to a dependency map."""
import json
from pathlib import Path
import sys


def add_comparisons(result, raw):
    profile = raw.get("comparison_profile")
    if not profile:
        return
    numbers = {name: i for i, name in enumerate(result["tests"])}
    sets = {value: i for i, value in enumerate(result["sets"])}

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

    result["comparison_types"] = profile["types"]
    for description in result["comparison_types"].values():
        # CPython can expose dedicated NoneType eq/ne slots rather than the
        # object slots. This immutable singleton still has identity equality.
        if description["name"] == "builtins:NoneType":
            description["kind"] = "identity"
    if "executed" in raw:
        completed = set(raw["executed"])
    else:
        # Older raw maps may list collected-but-deselected tests. Only contexts
        # actually observed during execution provide usable type evidence.
        completed = {context for lines in raw["lines"].values() for contexts in lines.values() for context in contexts if context in numbers}
    completed -= set(raw["reports"])
    result["comparison_profiled_tests"] = intern(completed)
    result["comparisons"] = {site: {"global": data["global"], "pairs": [[a, b, intern(contexts)] for a, b, contexts in data["pairs"]]} for site, data in profile["sites"].items()}
    result["provenance"]["comparison_profiles"] = "operand types from pristine identity operations; no changed operation is evaluated during collection"


if __name__ == "__main__":
    path = Path(sys.argv[1])
    result = json.loads(path.read_text("utf-8"))
    add_comparisons(result, json.loads(Path(sys.argv[2]).read_text("utf-8")))
    path.write_text(json.dumps(result, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"{len(result.get('comparisons', {}))} identity sites; map {path.stat().st_size:,} bytes")
