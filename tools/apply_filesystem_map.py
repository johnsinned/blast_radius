"""Compile complete file access and exact SQL-loader input-view evidence."""
import ast
from collections import Counter, defaultdict
import json
import posixpath
from pathlib import Path
import re
import sys
try:
    from .map_format import decode_mask
except ImportError:
    from map_format import decode_mask


def trace_access(lines, inventory, root="/opt/sqlglot", details=False):
    accessed, metadata, opens = set(), set(), Counter()
    metadata_counts, required = Counter(), set()
    names = defaultdict(set)
    for path in inventory: names[posixpath.basename(path)].add(path)

    def source_name(path):
        if "/__pycache__/" in path and path.endswith((".pyc", ".pyo")):
            folder, name = path.rsplit("/__pycache__/", 1)
            path = folder + "/" + name.split(".cpython-", 1)[0].split(".pypy", 1)[0] + ".py"
        elif path.endswith((".pyc", ".pyo")):
            path = path[:-1]
        return posixpath.normpath(path)

    def absolute(path):
        path = source_name(path)
        if path.startswith(root + "/"):
            relative = path[len(root) + 1:]
            return {relative} if relative in inventory else set()
        return set()

    for line in lines:
        call = re.search(r"\b([a-z][a-z0-9_]*)\(", line)
        if not call: continue
        syscall = call[1]
        descriptors = re.findall(r"<(/[^>]*)>", line)
        for descriptor in descriptors: accessed.update(absolute(descriptor.removesuffix(" (deleted)")))
        paths = set()
        for quoted in re.findall(r'"(?:\\.|[^"\\])*"', line):
            value = ast.literal_eval(quoted)
            if not value: continue
            if value.startswith("/"):
                paths.update(absolute(value))
            elif descriptors:
                # At worst this associates a relative name with extra FDs;
                # it cannot turn an observed file into an unobserved one.
                for directory in descriptors: paths.update(absolute(posixpath.join(directory, value)))
            else:
                # Older strace/kernel combinations do not annotate AT_FDCWD.
                # Match every possible pinned basename rather than guessing cwd.
                paths.update(names.get(posixpath.basename(source_name(value)), set()))
        accessed.update(paths)
        if syscall in ("open", "openat", "openat2", "creat"):
            opens.update(paths)
            required.update(paths)
            for descriptor in descriptors: required.update(absolute(descriptor))
        elif syscall in ("stat", "lstat", "stat64", "lstat64", "fstatat64", "newfstatat", "statx", "access", "faccessat", "faccessat2", "readlink", "readlinkat"):
            metadata.update(paths)
            metadata_counts.update(paths)
        else:
            required.update(paths)
            for descriptor in descriptors: required.update(absolute(descriptor))
    result = accessed, metadata, opens
    return (*result, metadata_counts, required) if details else result


def update_filesystem(result, directory):
    inventory = json.loads((directory / "inventory.json").read_text())
    raw = json.loads((directory / "python-files.json").read_text())
    if raw["exit"] or len(set(raw["executed"])) < 1600 or any("failed" in reports for reports in raw["reports"].values()):
        raise ValueError("filesystem collection must pass the entire pristine suite")
    if raw["helper_sha256"] != result["files"]["tests/helpers.py"]["sha256"]:
        raise ValueError("fixture loader differs from the pinned map")
    with (directory / "files.strace").open() as lines:
        accessed, metadata, opens, metadata_counts, required = trace_access(lines, inventory, details=True)
    if re.search(r"(?m)^strace:", (directory / "pytest.log").read_text()):
        raise ValueError("filesystem tracer reported an incomplete or unsupported operation")
    # Independent positive observations always override an absence in the
    # syscall parser, including imports opened through bytecode caches.
    accessed.update(path for path in raw["opens"] if path in inventory)
    known = {path for path, data in result["files"].items() if path in inventory and (decode_mask(result["sets"][data["reads"]]) or decode_mask(result["sets"][data["coverage"]]))}
    accessed.update(known)
    required.update(known)
    required.update(path for path in raw["opens"] if path in inventory)
    sensitive = {path for path in metadata if metadata_counts[path] != raw.get("safe_stats", {}).get(path, 0)}
    required.update(sensitive)
    result["file_inventory"] = {path: {"sha256": digest, "accessed": path in accessed, "mode_only": path in accessed and path not in required} for path, digest in inventory.items()}
    result["fixture_views"] = {path: "sql_comment_lines" for path, data in raw["opens"].items() if path in inventory and data["opens"] > 0 and not data["raw"] and path not in sensitive and opens[path] == data["opens"]}
    result["provenance"]["filesystem_validation"] = "pristine full-suite native strace file operations, descendants included; Python opens independently classified; SQL views require exact loader fingerprint, matching open counts, and only mode/existence pathname checks with exact native/Python counts"


if __name__ == "__main__":
    path, directory = map(Path, sys.argv[1:3])
    result = json.loads(path.read_text("utf-8"))
    update_filesystem(result, directory)
    path.write_text(json.dumps(result, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"indexed {len(result['file_inventory'])} files; {sum(not d['accessed'] for d in result['file_inventory'].values())} unaccessed; {len(result['fixture_views'])} exact fixture views; {path.stat().st_size:,} bytes")
