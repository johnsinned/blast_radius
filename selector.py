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
import types
from semantic_values import compare, restore, snapshot, dictionary_key_id, interval_equivalent

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


def semantic_changes(before, after):
    """Match a whole-file edit made only of supported local pure expressions."""
    changes = []

    def equal(a, b):
        return ast.dump(a, include_attributes=False) == ast.dump(b, include_attributes=False)

    def literal(node):
        value = ast.literal_eval(node)
        snapshot(value)
        return value

    def same(a, b):
        if isinstance(a, ast.AST):
            if type(a) is not type(b):
                return False
            if isinstance(a, (ast.Assign, ast.AnnAssign)) and a.value is not None and not isinstance(a.value, ast.Dict) and not equal(a, b):
                target = a.target if isinstance(a, ast.AnnAssign) else a.targets[0] if len(a.targets) == 1 else None
                if isinstance(target, ast.Name) and all(same(value, getattr(b, key)) for key, value in ast.iter_fields(a) if key != "value"):
                    try:
                        literal(a.value)
                        literal(b.value)
                    except (ValueError, TypeError, SyntaxError, RecursionError):
                        pass
                    else:
                        changes.append(("attribute", a, b))
                        return True
            if isinstance(a, ast.Compare) and len(a.ops) == len(b.ops) == 1 and not equal(a, b):
                operands_same = equal(a.left, b.left) and equal(a.comparators[0], b.comparators[0])
                if not operands_same and any(isinstance(op, (ast.Is, ast.IsNot)) for op in a.ops + b.ops):
                    return False
                try:
                    for old, new in ((a.left, b.left), (a.comparators[0], b.comparators[0])):
                        if not equal(old, new):
                            literal(old)
                            literal(new)
                except (ValueError, TypeError, SyntaxError, RecursionError):
                    return False
                changes.append(("comparison", a, b))
                return True
            if isinstance(a, ast.Dict) and not equal(a, b):
                if len(a.keys) != len(b.keys) or any(x is None or y is None or not equal(x, y) for x, y in zip(a.keys, b.keys)):
                    return False
                try:
                    keys = [dictionary_key_id(literal(key)) for key in a.keys]
                    if None in keys or len(keys) != len(set(keys)):
                        return False
                    for old, new in zip(a.values, b.values):
                        if not equal(old, new):
                            literal(old)
                            literal(new)
                except (ValueError, TypeError, SyntaxError, RecursionError):
                    return False
                changes.append(("dictionary", a, b))
                return True
            return all(same(value, getattr(b, key)) for key, value in ast.iter_fields(a))
        if isinstance(a, list):
            return isinstance(b, list) and len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
        return type(a) is type(b) and a == b

    return changes if same(before, after) and changes else None


def changed_function_bodies(before, after):
    """Match edits wholly within existing bodies, preserving their public metadata."""
    changed = []

    def equal(a, b):
        return ast.dump(a, include_attributes=False) == ast.dump(b, include_attributes=False)

    def same(a, b, scope=()):
        if isinstance(a, ast.AST):
            if type(a) is not type(b):
                return False
            if equal(a, b):
                return True
            if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = (*scope, a.name)
                if any(not same(value, getattr(b, key), scope) for key, value in ast.iter_fields(a) if key != "body"):
                    return False
                if ast.get_docstring(a, clean=False) != ast.get_docstring(b, clean=False):
                    return False
                saved = len(changed)
                if same(a.body, b.body, name):
                    return True
                del changed[saved:]
                changed.append(".".join(name))
                return True
            child_scope = (*scope, a.name) if isinstance(a, ast.ClassDef) else scope
            return all(same(value, getattr(b, key), child_scope) for key, value in ast.iter_fields(a))
        if isinstance(a, list):
            return isinstance(b, list) and len(a) == len(b) and all(same(x, y, scope) for x, y in zip(a, b))
        return type(a) is type(b) and a == b

    return changed if same(before, after) and changed else None


def code_layout(tree):
    """Compilation only: check flags and closure/local layout without executing code."""
    result = {}

    def walk(code):
        signature = (code.co_flags, code.co_freevars, code.co_cellvars, code.co_varnames)
        result.setdefault(code.co_qualname, []).append(signature)
        for value in code.co_consts:
            if isinstance(value, types.CodeType):
                walk(value)

    walk(compile(tree, "<blast-radius-analysis>", "exec", dont_inherit=True))
    return result


class Selector:
    def __init__(self, tests: list[dict], source: Path, map_path: Path):
        self.tests, self.source = tests, source
        self.all = (1 << len(tests)) - 1
        self.cache = {}
        self.value_cache = {}
        self.map = json.loads(map_path.read_text("utf-8")) if map_path.exists() else None
        if self.map and self.map.get("pin") != PIN:
            raise ValueError("dependency map targets a different sqlglot commit")
        self.masks = [int(s, 16) if isinstance(s, str) else sum(1 << n for n in s) for s in self.map["sets"]] if self.map else []
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

    def integer_equivalent_tests(self, site, comparison, replacement):
        profile = self.map.get("integer_ranges", {}).get(site)
        if not profile or profile["global"]:
            return 0

        def integer(node):
            try:
                value = ast.literal_eval(node)
                return value if type(value) is int else None
            except (ValueError, TypeError, SyntaxError, RecursionError):
                return None

        old_left, old_right = comparison.left, comparison.comparators[0]
        new_left, new_right = replacement.left, replacement.comparators[0]
        if ast.dump(old_left) == ast.dump(new_left) and integer(old_right) is not None and integer(new_right) is not None:
            variable_left, before, after = True, integer(old_right), integer(new_right)
        elif ast.dump(old_right) == ast.dump(new_right) and integer(old_left) is not None and integer(new_left) is not None:
            variable_left, before, after = False, integer(old_left), integer(new_left)
        else:
            return 0
        safe = self.mask(self.map.get("integer_profiled_tests")) & ~self.mask(profile["unknown"])
        operation, changed = type(comparison.ops[0]).__name__, type(replacement.ops[0]).__name__
        for low_left, high_left, low_right, high_right, contexts in profile["groups"]:
            low, high = (low_left, high_left) if variable_left else (low_right, high_right)
            constant = (low_right, high_right) if variable_left else (low_left, high_left)
            if constant != (before, before) or not interval_equivalent(operation, changed, low, high, before, after, variable_left):
                safe &= ~self.mask(contexts)
        return safe

    def equivalent_tests(self, path, before, after):
        changes = semantic_changes(before, after)
        if not changes:
            return 0
        profiled = self.mask(self.map.get("comparison_profiled_tests"))
        safe = self.all
        types = self.map.get("comparison_types", {})

        def proof_unchanged(description):
            if path not in description.get("proof_files", []):
                return True
            spans = description.get("proof_spans", {}).get(path)
            return bool(spans) and not any(start <= node.end_lineno and node.lineno <= end for _, node, _ in changes for start, end in spans)

        for kind, comparison, replacement in changes:
            site = f"{path}:{comparison.lineno}:{comparison.col_offset}"
            if kind == "attribute":
                profile = self.map.get("attributes", {}).get(site)
                if not profile or not profile["eligible"] or profile["global"]:
                    return 0
                safe &= self.mask(self.map.get("attribute_profiled_tests")) & ~self.mask(profile["reads"])
                continue
            safe &= profiled
            if kind == "dictionary":
                profile = self.map.get("dictionaries", {}).get(site)
                if not profile or profile["global_whole"]:
                    return 0
                keys = [dictionary_key_id(ast.literal_eval(key)) for key, a, b in zip(comparison.keys, comparison.values, replacement.values) if ast.dump(a) != ast.dump(b)]
                if any(key in profile["global_keys"] for key in keys):
                    return 0
                affected = self.mask(profile["whole"])
                for key in keys:
                    affected |= self.mask(profile["keys"].get(key))
                safe &= ~affected
                continue
            profile = self.map.get("comparisons", {}).get(site)
            if profile and profile["global"]:
                return 0
            integer_safe = self.integer_equivalent_tests(site, comparison, replacement)
            if not profile or not profile["pairs"]:
                safe &= integer_safe
                continue
            operands_same = ast.dump(comparison.left) == ast.dump(replacement.left) and ast.dump(comparison.comparators[0]) == ast.dump(replacement.comparators[0])
            op, new_op = type(comparison.ops[0]).__name__, type(replacement.ops[0]).__name__
            matching = (op, new_op) in (("Is", "Eq"), ("Eq", "Is"), ("IsNot", "NotEq"), ("NotEq", "IsNot"))
            type_safe = profiled if operands_same and matching else 0
            for left, right, contexts in profile["pairs"]:
                a, b = types[left]["kind"], types[right]["kind"]
                # object equality on both sides is identity equality. Canonical
                # str-enum members also agree, within the same enum class.
                equivalent = (a == b == "identity") or (left == right and a == "canonical_str_enum")
                if types[left].get("name") == "builtins:NoneType" and b in ("identity", "builtin_value", "canonical_str_enum"):
                    equivalent = True
                if types[right].get("name") == "builtins:NoneType" and a in ("identity", "builtin_value", "canonical_str_enum"):
                    equivalent = True
                equality = op in ("Is", "Eq")
                for none_type, custom in ((types[left], types[right]), (types[right], types[left])):
                    if none_type.get("name") == "builtins:NoneType" and custom.get("kind") == "custom_none" and proof_unchanged(custom):
                        equivalent = bool(custom.get("none_eq") if equality else custom.get("none_ne"))
                if not equivalent:
                    type_safe &= ~self.mask(contexts)
            value_safe = 0
            if "values" in profile:
                value_safe = profiled & ~self.mask(profile["unknown"])
                for left_id, right_id, alias, contexts in profile["values"]:
                    try:
                        for value_id in (left_id, right_id):
                            if value_id not in self.value_cache:
                                self.value_cache[value_id] = restore(self.map["primitive_values"][value_id])
                        left, right = self.value_cache[left_id], self.value_cache[right_id]
                        new_left = left if ast.dump(comparison.left) == ast.dump(replacement.left) else ast.literal_eval(replacement.left)
                        new_right = right if ast.dump(comparison.comparators[0]) == ast.dump(replacement.comparators[0]) else ast.literal_eval(replacement.comparators[0])
                        if op in ("Is", "IsNot") or new_op in ("Is", "IsNot"):
                            # Allocation/interning can vary between runs. Require
                            # agreement for every alias relation compatible with
                            # the primitive values, rather than trusting one run.
                            aliases = (False,)
                            if type(left) is type(right) and left == right:
                                aliases = (True,) if left is None or type(left) is bool else (False, True)
                            equivalent = all(compare(op, left, right, possible) == compare(new_op, new_left, new_right, possible) for possible in aliases)
                        else:
                            equivalent = compare(op, left, right) == compare(new_op, new_left, new_right)
                    except (TypeError, ValueError, OverflowError, KeyError, RecursionError):
                        equivalent = False
                    if not equivalent:
                        value_safe &= ~self.mask(contexts)
            safe &= type_safe | value_safe | integer_safe
        return safe

    def body_callers(self, info, before, after):
        if not self.map.get("global_functions_tracked"):
            return None
        changed = changed_function_bodies(before, after)
        if not changed:
            return None
        scopes = {scope["name"]: scope for scope in info["scopes"] if scope["kind"] == "function"}
        if any(name not in scopes or scopes[name].get("global_call") for name in changed):
            return None
        callers = 0
        for name in changed:
            callers |= self.mask(scopes[name]["coverage"])
        if callers:
            # Only the zero-call case changes selection. Avoid recompiling
            # two whole modules for an executed function's ordinary edit.
            return callers
        try:
            if code_layout(before) != code_layout(after):
                return None
        except (SyntaxError, ValueError, RecursionError):
            return None
        return callers

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
        body_callers = self.body_callers(info, before, after)
        if body_callers == 0:
            return reads, "unchanged function metadata; bodies not executed"

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
            return (result & ~equivalent) | reads, "baseline observations certify local equivalence"
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
