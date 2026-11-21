"""Select tests using only pristine-checkout dependency maps and runtime patches."""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass, field
import json
import hashlib
import io
import math
from pathlib import Path, PurePosixPath
import re
import sys
import tokenize

PIN = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Change:
    path: str
    hunks: list = field(default_factory=list)
    old_lines: set[int] = field(default_factory=set)
    anchors: set[int] = field(default_factory=set)
    added: bool = False
    deleted: bool = False
    unsupported: bool = False


def parse_patch(text: str) -> list[Change]:
    changes = []
    change = hunk = None
    old_line = 0
    edit_start = None
    for line in text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            change = Change("")
            changes.append(change)
            hunk = None
        elif change is None:
            continue
        elif line.startswith("--- "):
            raw = line[4:].rstrip("\r\n")
            change.added = raw == "/dev/null"
            if raw.startswith("a/"):
                change.path = raw[2:]
        elif line.startswith("+++ "):
            raw = line[4:].rstrip("\r\n")
            change.deleted = raw == "/dev/null"
            if raw.startswith("b/"):
                change.path = raw[2:]
            elif raw != "/dev/null":
                change.unsupported = True
        elif match := HUNK.match(line):
            old_line = int(match[1])
            edit_start = None
            hunk = [old_line, []]
            change.hunks.append(hunk)
        elif line.startswith("\\ No newline"):
            if hunk and hunk[1]:
                hunk[1][-1] = hunk[1][-1].rstrip("\r\n")
        elif hunk is not None and line[:1] in (" ", "+", "-"):
            hunk[1].append(line)
            if line.startswith("-"):
                if edit_start is None:
                    edit_start = old_line
                change.old_lines.add(old_line)
                old_line += 1
            elif line.startswith("+"):
                if edit_start is None:
                    edit_start = old_line
                change.anchors.add(edit_start)
            else:
                edit_start = None
                old_line += 1
        elif line.startswith(("Binary files", "GIT binary patch", "rename from", "rename to")):
            change.unsupported = True
    return changes


def apply_change(source: str, change: Change) -> str:
    old = source.splitlines(keepends=True)
    result = []
    cursor = 0
    for start, lines in change.hunks:
        position = max(0, start - 1)
        if position < cursor:
            raise ValueError("overlapping hunks")
        result.extend(old[cursor:position])
        cursor = position
        for line in lines:
            if line.startswith((" ", "-")):
                if cursor >= len(old) or old[cursor].rstrip("\r\n") != line[1:].rstrip("\r\n"):
                    raise ValueError("patch does not match pinned source")
                if line.startswith(" "):
                    result.append(old[cursor])
                cursor += 1
            else:
                result.append(line[1:])
    result.extend(old[cursor:])
    return "".join(result)


def split(tests: list[dict], worker_count: int) -> list[list[int]]:
    workers = [[] for _ in range(worker_count)]
    loads = [0] * worker_count
    for test in sorted(tests, key=lambda t: (-math.floor(t["seconds"] * 1000 + 0.5), t["number"])):
        lightest = loads.index(min(loads))
        workers[lightest].append(test["number"])
        loads[lightest] += math.floor(test["seconds"] * 1000 + 0.5)
    return [w for w in workers if w]


def schedule(tests: list[dict]) -> list[list[int]]:
    if not tests:
        return []
    durations = {t["number"]: math.floor(t["seconds"] * 1000 + 0.5) for t in tests}
    best = None
    for count in range(1, min(8, len(tests)) + 1):
        workers = split(tests, count)
        cost = max(sum(durations[n] for n in w) for w in workers) + 2000 * len(workers)
        if best is None or cost < best[0]:
            best = (cost, workers)
    return best[1]


def identity_only_changes(before, after):
    """Return changed comparisons only if every AST difference preserves polarity."""
    changes = []

    def same(left, right):
        if isinstance(left, ast.AST):
            if type(left) is not type(right):
                return False
            if isinstance(left, ast.Compare) and len(left.ops) == len(right.ops) == 1:
                a, b = type(left.ops[0]), type(right.ops[0])
                if a is not b:
                    if (a, b) not in ((ast.Is, ast.Eq), (ast.IsNot, ast.NotEq)):
                        return False
                    changes.append(left)
                    return same(left.left, right.left) and same(left.comparators, right.comparators)
            return all(same(value, getattr(right, key)) for key, value in ast.iter_fields(left))
        if isinstance(left, list):
            return isinstance(right, list) and len(left) == len(right) and all(same(a, b) for a, b in zip(left, right))
        return type(left) is type(right) and left == right

    return changes if same(before, after) and changes else None


class Selector:
    def __init__(self, tests: list[dict], source: Path, map_path: Path):
        self.tests, self.source = tests, source
        self.all = (1 << len(tests)) - 1
        self.cache = {}
        self.map = json.loads(map_path.read_text("utf-8")) if map_path.exists() else None
        if self.map and self.map.get("pin") != PIN:
            raise ValueError("dependency map targets a different sqlglot commit")
        self.masks = [int(s, 16) for s in self.map["sets"]] if self.map else []
        # Match by pytest id; the scorer controls test numbers.
        self.numbers = {t["id"]: i for i, t in enumerate(tests)}
        self.translate = [self.numbers.get(name) for name in self.map["tests"]] if self.map else []
        known = set(self.map["tests"]) if self.map else set()
        self.unknown = sum(1 << i for name, i in self.numbers.items() if name not in known)

    def mask(self, index):
        if index is None:
            return 0
        if index not in self.cache:
            old, result = self.masks[index], 0
            while old:
                bit = old & -old
                target = self.translate[bit.bit_length() - 1]
                if target is not None:
                    result |= 1 << target
                old ^= bit
            self.cache[index] = result
        return self.cache[index]

    def line_mask(self, info, lines):
        result = 0
        for line in lines:
            result |= self.mask(info.get("lines", {}).get(str(line)))
        return result

    def read_source(self, path):
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts or "\\" in path or ":" in path:
            raise ValueError("unsafe patch path")
        return self.source.joinpath(*relative.parts).read_text("utf-8")

    def equivalent_tests(self, path, before, after):
        changes = identity_only_changes(before, after)
        if not changes or "comparisons" not in self.map:
            return 0
        safe = self.mask(self.map.get("comparison_profiled_tests"))
        types = self.map["comparison_types"]
        for comparison in changes:
            site = f"{path}:{comparison.lineno}:{comparison.col_offset}"
            profile = self.map["comparisons"].get(site)
            if not profile or profile["global"] or not profile["pairs"]:
                return 0
            for left, right, contexts in profile["pairs"]:
                a, b = types[left]["kind"], types[right]["kind"]
                # object equality on both sides is identity equality. Canonical
                # str-enum members also agree, within the same enum class.
                equivalent = (a == b == "identity") or (left == right and a == "canonical_str_enum")
                if not equivalent:
                    safe &= ~self.mask(contexts)
        return safe

    def select_file(self, change, allow_equivalence=True):
        if not self.map or change.unsupported or not change.path:
            return self.all, "fallback"
        info = self.map["files"].get(change.path)
        if change.added or change.deleted or not change.hunks:
            return self.all, "structural change"
        if not info:
            return self.all, "unmapped file"
        reads = self.mask(info.get("reads"))
        if not change.path.endswith(".py"):
            return reads or self.all, "file readers"
        try:
            source = self.read_source(change.path)
            if info.get("sha256") and hashlib.sha256(source.encode()).hexdigest() != info["sha256"]:
                return self.all, "source/map mismatch"
            patched = apply_change(source, change)
            old_encoding = tokenize.detect_encoding(io.BytesIO(source.encode("utf-8")).readline)[0]
            new_encoding = tokenize.detect_encoding(io.BytesIO(patched.encode("utf-8")).readline)[0]
            if old_encoding != new_encoding:
                return self.all, "source encoding change"
            # Parsing bytes honors Python encoding cookies, which can make an
            # apparently comment-only edit change executable code.
            before, after = ast.parse(source.encode("utf-8")), ast.parse(patched.encode("utf-8"))
        except (SyntaxError, ValueError, OSError, RecursionError, UnicodeError):
            return self.all, "syntax or patch fallback"
        if ast.dump(before, include_attributes=False) == ast.dump(after, include_attributes=False):
            return reads, "same Python AST"
        equivalent = self.equivalent_tests(change.path, before, after) if allow_equivalence else 0

        changed = change.old_lines | change.anchors
        result = reads | self.line_mask(info, changed)
        for line in changed:
            scopes = [s for s in info["scopes"] if s["start"] <= line <= s["end"]]
            statements = [s for s in info["statements"] if s[0] <= line <= s[1]]
            if statements:
                start, end, header_end = min(statements, key=lambda s: s[1] - s[0])
                result |= self.line_mask(info, range(start, header_end + 1))
            functions = [s for s in scopes if s["kind"] == "function"]
            if functions:
                function = min(functions, key=lambda s: s["end"] - s["start"])
                if not change.old_lines or not result:
                    result |= self.mask(function["coverage"])
            else:
                assignments = [a for a in info["assignments"] if a["start"] <= line <= a["end"]]
                if assignments:
                    for assignment in assignments:
                        result |= self.mask(assignment["users"])
                else:
                    # Import changes can stop collection of tests whose bodies
                    # never execute this module, so ordinary coverage is too narrow.
                    return self.all, "import-time structural change"
        result = result or self.mask(info["coverage"]) or self.all
        if equivalent:
            return (result & ~equivalent) | reads, "baseline types certify comparison equivalence"
        return result, "baseline dependencies"

    def select(self, patch):
        changes = parse_patch(patch)
        if not changes:
            return self.tests, ["unparsed patch"]
        bits = self.unknown
        reasons = []
        for change in changes:
            selected, reason = self.select_file(change, allow_equivalence=len(changes) == 1)
            bits |= selected
            reasons.append(f"{change.path}: {reason}")
        return [t for i, t in enumerate(self.tests) if bits & (1 << i)], reasons


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("/data"))
    parser.add_argument("--source", type=Path, default=Path("/opt/sqlglot"))
    parser.add_argument("--map", type=Path, default=Path(__file__).parent / "maps/dependencies.json")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    tests = json.loads((args.data / "tests.json").read_text("utf-8"))["tests"]
    selector = Selector(tests, args.source, args.map)
    for path in sorted((args.data / "commits").glob("*.patch")):
        selected, reasons = selector.select(path.read_text("utf-8"))
        print(json.dumps({"commit": path.stem, "workers": schedule(selected)}, separators=(",", ":")))
        if args.verbose:
            print(f"{path.stem}: {len(selected)}/{len(tests)} tests; {'; '.join(reasons)}", file=sys.stderr)


if __name__ == "__main__":
    main()
