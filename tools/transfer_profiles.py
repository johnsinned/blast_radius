"""Retain measured scalar/key evidence while replacing ordinary coverage."""
import copy
import json
from pathlib import Path
import sys
from map_format import decode_mask, compact_masks


def transfer_profiles(base, previous):
    numbers = {name: i for i, name in enumerate(base["tests"])}
    translate = [numbers.get(name) for name in previous["tests"]]
    sets = {format(decode_mask(value), "x"): i for i, value in enumerate(base["sets"])}
    cache = {}

    def intern(bits):
        value = format(bits, "x")
        if value not in sets:
            sets[value] = len(base["sets"])
            base["sets"].append(value)
        return sets[value]

    def remap(index):
        if index not in cache:
            old, bits = decode_mask(previous["sets"][index]), 0
            while old:
                bit = old & -old
                target = translate[bit.bit_length() - 1]
                if target is not None:
                    bits |= 1 << target
                old ^= bit
            cache[index] = intern(bits)
        return cache[index]

    eligible = decode_mask(base["sets"][base["comparison_profiled_tests"]])
    old_eligible = decode_mask(base["sets"][remap(previous["comparison_profiled_tests"])])
    base["comparison_profiled_tests"] = intern(eligible & old_eligible)
    base["comparison_types"] = copy.deepcopy(previous["comparison_types"])
    base["primitive_values"] = copy.deepcopy(previous.get("primitive_values", []))
    base["comparisons"] = copy.deepcopy(previous["comparisons"])
    for data in base["comparisons"].values():
        for pair in data["pairs"]:
            pair[2] = remap(pair[2])
        if "values" in data:
            data["unknown"] = remap(data["unknown"])
            for value in data["values"]:
                value[3] = remap(value[3])
    base["dictionaries"] = copy.deepcopy(previous.get("dictionaries", {}))
    for data in base["dictionaries"].values():
        data["whole"] = remap(data["whole"])
        data["keys"] = {key: remap(mask) for key, mask in data["keys"].items()}
    # The independent read pass excludes importer I/O; bytecode inspection in
    # the pinned tests reads these same source files on both platforms.
    for path, data in base["files"].items():
        if path.endswith(".py") and path in previous["files"]:
            data["reads"] = remap(previous["files"][path]["reads"])
    base["provenance"]["retained_profiles"] = {"python": previous["provenance"]["python"], "platform": previous["provenance"]["platform"], "method": previous["provenance"].get("comparison_profiles", "baseline operands and key reads")}
    compact_masks(base)


if __name__ == "__main__":
    target, reference = map(Path, sys.argv[1:3])
    base = json.loads(target.read_text("utf-8"))
    transfer_profiles(base, json.loads(reference.read_text("utf-8")))
    target.write_text(json.dumps(base, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"transferred baseline profiles; {target.stat().st_size:,} bytes")
