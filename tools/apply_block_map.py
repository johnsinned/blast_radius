"""Attach pristine basic-block coverage to the ordinary dependency map."""
import json
from pathlib import Path
import sys
from map_format import decode_mask, compact_masks


def update_blocks(result, raw):
    profile = raw["bytecode_profile"]
    if not profile["valid"] or any(r["outcome"] == "failed" for reports in raw["reports"].values() for r in reports):
        raise ValueError("instruction collection must pass without tracking errors")
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

    result["bytecode_profile"] = {"python": profile["python"], "completed": intern(set(raw["executed"]) - set(raw["reports"])), "codes": {key: {"fingerprint": data["fingerprint"], "ambiguous": data["ambiguous"], "blocks": {offset: [intern(block["contexts"]), block["global"]] for offset, block in data["blocks"].items()}} for key, data in profile["codes"].items()}}
    result["provenance"]["block_validation"] = "pristine CPython instruction events, basic-block overapproximation, import-time tracking; exact compiler version and instruction layout required at runtime"
    compact_masks(result)


if __name__ == "__main__":
    path = Path(sys.argv[1])
    result = json.loads(path.read_text("utf-8"))
    update_blocks(result, json.loads(Path(sys.argv[2]).read_text("utf-8")))
    path.write_text(json.dumps(result, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"attached basic-block coverage; {path.stat().st_size:,} bytes")
