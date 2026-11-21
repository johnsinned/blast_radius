"""Index every pinned code object, including functions never entered in baseline."""
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bytecode_model import code_key, fingerprint, layout, walk_codes


def complete_codes(result, source):
    profile = result["bytecode_profile"]
    if profile["python"] != list(sys.version_info[:2]):
        raise ValueError("static index must use the profiled interpreter")
    zero = next(i for i, value in enumerate(result["sets"]) if value in ("0", []))
    codes = profile["codes"]
    for path in sorted((source / "sqlglot").rglob("*.py")):
        relative = path.relative_to(source).as_posix()
        text = path.read_text("utf-8")
        info = result["files"].get(relative)
        if not info or hashlib.sha256(text.encode()).hexdigest() != info["sha256"]:
            raise ValueError("static code index differs from pinned source")
        for code in walk_codes(compile(path.read_bytes(), str(path), "exec", dont_inherit=True)):
            key = relative + ":" + code_key(code)
            signature = fingerprint(code)
            if key in codes:
                if codes[key]["fingerprint"] != signature:
                    codes[key]["ambiguous"] = True
            else:
                _, blocks = layout(code)
                codes[key] = {"fingerprint": signature, "ambiguous": False, "blocks": {str(offset): [zero, False] for offset in set(blocks.values())}}
    profile["static_complete"] = True


if __name__ == "__main__":
    path, source = map(Path, sys.argv[1:3])
    result = json.loads(path.read_text("utf-8"))
    complete_codes(result, source)
    path.write_text(json.dumps(result, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"completed static instruction index; {path.stat().st_size:,} bytes")
