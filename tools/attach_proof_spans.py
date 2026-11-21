"""Protect every equality definition in the pinned proof files."""
import ast
import hashlib
import json
from pathlib import Path
import sys


def attach_spans(result, source):
    paths = {path for description in result["comparison_types"].values() for path in description.get("proof_files", [])}
    spans = {}
    for path in sorted(paths):
        text = (source / path).read_text("utf-8")
        if hashlib.sha256(text.encode()).hexdigest() != result["files"][path]["sha256"]:
            raise ValueError("proof source differs from the pinned baseline")
        spans[path] = [[min([node.lineno] + [d.lineno for d in node.decorator_list]), node.end_lineno] for node in ast.walk(ast.parse(text)) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ("__eq__", "__ne__")]
        if not spans[path]:
            raise ValueError("proof file contains no protected equality methods")
    for description in result["comparison_types"].values():
        if "proof_files" in description:
            description["proof_spans"] = {path: spans[path] for path in description["proof_files"]}


if __name__ == "__main__":
    path, source = map(Path, sys.argv[1:3])
    result = json.loads(path.read_text("utf-8"))
    attach_spans(result, source)
    path.write_text(json.dumps(result, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"attached protected equality spans; {path.stat().st_size:,} bytes")
