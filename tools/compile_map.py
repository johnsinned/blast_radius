"""Compile pristine-checkout observations into a compact, portable plain JSON map."""
import argparse
import ast
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import platform
import subprocess
from compile_comparisons import add_comparisons
from map_format import compact_masks


def compile_map(root, raw, output, archive=False):
    tests = raw["tests"]
    numbers = {name: i for i, name in enumerate(tests)}

    def bits(contexts):
        result = 0
        for context in contexts:
            for name in raw["setup"].get(context, [context]):
                if name in numbers:
                    result |= 1 << numbers[name]
        return result

    sets, interned = [], {}

    def intern(value):
        if value not in interned:
            interned[value] = len(sets)
            sets.append(format(value, "x"))
        return interned[value]

    line_maps = {p: {int(n): bits(cs) for n, cs in lines.items()} for p, lines in raw["lines"].items()}
    attr_users, name_users = defaultdict(int), defaultdict(int)
    trees = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if not rel.startswith(("sqlglot/", "tests/")):
            continue
        try:
            tree = ast.parse(path.read_text("utf-8"))
        except (SyntaxError, UnicodeError):
            continue
        trees[rel] = tree
        lm = line_maps.get(rel, {})
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                attr_users[node.attr] |= lm.get(node.lineno, 0)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                name_users[(rel, node.id)] |= lm.get(node.lineno, 0)

    files = {}
    for rel in sorted(set(trees) | set(raw["files"]) | set(line_maps)):
        lm = line_maps.get(rel, {})

        def covered(start, end):
            result = 0
            for n, value in lm.items():
                if start <= n <= end:
                    result |= value
            return result

        info = {"lines": {str(n): intern(b) for n, b in sorted(lm.items()) if b}, "reads": intern(bits(raw["files"].get(rel, []))), "coverage": intern(covered(1, 10**9)), "scopes": [], "statements": [], "assignments": []}
        files[rel] = info
        if rel not in trees:
            continue
        info["sha256"] = hashlib.sha256((root / rel).read_text("utf-8").encode()).hexdigest()
        module = rel[:-3].replace("/", ".").removesuffix(".__init__")

        def visit(node, scope=(), in_function=False):
            is_class = isinstance(node, ast.ClassDef)
            is_func = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            new_scope = scope
            if is_class or (is_func and not isinstance(node, ast.Lambda)):
                new_scope = (*scope, node.name)
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                info["scopes"].append({"start": start, "end": node.end_lineno, "kind": "class" if is_class else "function", "name": ".".join(new_scope), "coverage": intern(covered(start, node.end_lineno))})
            if isinstance(node, ast.stmt):
                header = node.lineno
                if isinstance(node, (ast.If, ast.While, ast.Assert)):
                    header = node.test.end_lineno
                elif isinstance(node, (ast.For, ast.AsyncFor)):
                    header = node.iter.end_lineno
                elif not hasattr(node, "body"):
                    header = node.end_lineno
                info["statements"].append([node.lineno, node.end_lineno, header])
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and not in_function:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name)}
                users = 0
                for name in names:
                    users |= attr_users[name] | name_users[(rel, name)]
                if scope:
                    class_users = bits(raw["classes"].get(module + ":" + ".".join(scope), []))
                    if class_users:
                        users = (users & class_users) or class_users
                info["assignments"].append({"start": node.lineno, "end": node.end_lineno, "names": sorted(names), "users": intern(users)})
            for child in ast.iter_child_nodes(node):
                visit(child, new_scope, in_function or is_func)

        visit(trees[rel])
    pin = (root / ".blast-radius-pin").read_text().strip() if archive else subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if len(tests) < 1600:
        raise SystemExit("incomplete pytest collection; fix collection errors before compiling")
    reports = {name: sorted({r["outcome"] for r in reports}) for name, reports in raw["reports"].items()}
    result = {"version": 1, "pin": pin, "provenance": {"python": platform.python_version(), "platform": platform.system(), "method": "pristine pytest line coverage, file reads, constructor receivers, static AST dependencies; process-pool jobs executed synchronously with input/output pickle round trips", "test_count": len(tests), "baseline_reports": reports}, "tests": tests, "sets": sets, "files": files}
    add_comparisons(result, raw)
    compact_masks(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"{output}: {output.stat().st_size:,} bytes; {len(files)} files; {len(sets)} distinct test sets")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("raw", type=Path)
    parser.add_argument("--output", type=Path, default=Path("maps/dependencies.json"))
    parser.add_argument("--archive", action="store_true")
    args = parser.parse_args()
    compile_map(args.source.resolve(), json.loads(args.raw.read_text("utf-8")), args.output, args.archive)
