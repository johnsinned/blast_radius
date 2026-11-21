"""Attach independently observed reads of literal class settings."""
import json
from pathlib import Path
import sys
from map_format import decode_mask, compact_masks


def update_attributes(result, raw):
    if any(report["outcome"] == "failed" for reports in raw["reports"].values() for report in reports):
        raise ValueError("setting-read collection must pass the pristine baseline")
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

    result["attribute_profiled_tests"] = intern(set(raw["executed"]) - set(raw["reports"]))
    # A lazily imported module may cache a setting for subsequent tests. The
    # attribute loader reads source directly; conservatively promote every
    # setting read in a source-opening context to an import-time dependency.
    import_contexts = set() if raw.get("attribute_imports_tracked") else {context for path, contexts in raw["files"].items() if path.startswith("sqlglot/") and path.endswith(".py") for context in contexts}
    result["attributes"] = {site: {"eligible": data["eligible"], "global": data["global"] or bool(import_contexts.intersection(data["reads"])), "reads": intern(data["reads"])} for site, data in raw["attribute_profile"].items()}
    for site, data in result["attributes"].items():
        path, line, _ = site.rsplit(":", 2)
        if any(scope["kind"] == "class" and scope.get("decorated") and scope["start"] <= int(line) <= scope["end"] for scope in result["files"].get(path, {}).get("scopes", [])):
            data["eligible"] = False
    result["provenance"]["attribute_validation"] = "independent pristine Linux pass; class settings read by library and tests; metaclass namespace consumption guarded"
    compact_masks(result)


if __name__ == "__main__":
    path = Path(sys.argv[1])
    result = json.loads(path.read_text("utf-8"))
    update_attributes(result, json.loads(Path(sys.argv[2]).read_text("utf-8")))
    path.write_text(json.dumps(result, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"updated setting reads; {path.stat().st_size:,} bytes")
