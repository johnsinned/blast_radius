"""Attach baseline operand-type profiles to a dependency map."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from map_format import decode_mask, compact_masks
from semantic_values import BUILTIN_NONE_EQUALITY_TYPES


def add_comparisons(result, raw):
    profile = raw.get("comparison_profile")
    if not profile:
        return
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

    result["comparison_types"] = profile["types"]
    builtin_names = {cls.__module__ + ":" + cls.__qualname__ for cls in BUILTIN_NONE_EQUALITY_TYPES}
    for description in result["comparison_types"].values():
        # CPython can expose dedicated NoneType eq/ne slots rather than the
        # object slots. This immutable singleton still has identity equality.
        if description["name"] == "builtins:NoneType":
            description["kind"] = "identity"
        elif description["name"] in builtin_names:
            description["kind"] = "builtin_value"
    if "executed" in raw:
        completed = set(raw["executed"])
    else:
        # Older raw maps may list collected-but-deselected tests. Only contexts
        # actually observed during execution provide usable type evidence.
        completed = {context for lines in raw["lines"].values() for contexts in lines.values() for context in contexts if context in numbers}
    completed -= set(raw["reports"])
    result["comparison_profiled_tests"] = intern(completed)
    values, value_ids = [], {}

    def intern_value(value):
        key = json.dumps(value, separators=(",", ":"))
        if key not in value_ids:
            value_ids[key] = len(values)
            values.append(value)
        return value_ids[key]

    result["comparisons"] = {}
    for site, data in profile["sites"].items():
        compiled = {"global": data["global"], "pairs": [[a, b, intern(contexts)] for a, b, contexts in data["pairs"]]}
        if "values" in data:
            compiled["values"] = [[intern_value(a), intern_value(b), alias, intern(contexts)] for a, b, alias, contexts in data["values"]]
            compiled["unknown"] = intern(data["unknown"])
        result["comparisons"][site] = compiled
    result["primitive_values"] = values
    result["dictionaries"] = {site: {"global_whole": data["global_whole"], "global_keys": data["global_keys"], "whole": intern(data["whole"]), "keys": {key: intern(contexts) for key, contexts in data["keys"].items()}} for site, data in raw.get("dictionary_profile", {}).items()}
    result["provenance"]["comparison_profiles"] = "original operand types and bounded primitive fixtures; no changed operation is evaluated during collection"


if __name__ == "__main__":
    path = Path(sys.argv[1])
    result = json.loads(path.read_text("utf-8"))
    add_comparisons(result, json.loads(Path(sys.argv[2]).read_text("utf-8")))
    compact_masks(result)
    path.write_text(json.dumps(result, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"{len(result.get('comparisons', {}))} identity sites; map {path.stat().st_size:,} bytes")
