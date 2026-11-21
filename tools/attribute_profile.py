"""Track reads of literal class settings without changing their runtime values."""
import ast
from collections.abc import Mapping
import inspect
from pathlib import Path
import sys

try:
    from semantic_values import snapshot
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from semantic_values import snapshot

CURRENT = lambda: ""
FIELDS = {}
DECLARATIONS = {}
NAMES = set()
UNKNOWN = {}
META_CACHE = {}
IMPORT_DEPTH = 0


def _name(cls):
    return type.__getattribute__(cls, "__module__") + ":" + type.__getattribute__(cls, "__qualname__").replace(".<locals>.", ".")


def index(root):
    for path in sorted((root / "sqlglot").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        module = relative[:-3].replace("/", ".").removesuffix(".__init__")
        tree = ast.parse(path.read_text("utf-8"))

        def visit(node, scope=()):
            if isinstance(node, ast.ClassDef):
                scope = (*scope, node.name)
                owner = module + ":" + ".".join(scope)
                for statement in node.body:
                    target = statement.target if isinstance(statement, ast.AnnAssign) else statement.targets[0] if isinstance(statement, ast.Assign) and len(statement.targets) == 1 else None
                    value = statement.value if isinstance(statement, (ast.Assign, ast.AnnAssign)) else None
                    if not isinstance(target, ast.Name) or value is None or isinstance(value, ast.Dict) or target.id.startswith("__"):
                        continue
                    try:
                        literal = ast.literal_eval(value)
                        snapshot(literal)
                    except (ValueError, TypeError, SyntaxError, RecursionError):
                        continue
                    site = f"{relative}:{statement.lineno}:{statement.col_offset}"
                    FIELDS[site] = {"name": target.id, "value": literal, "decorated": bool(node.decorator_list), "eligible": False, "global": False, "reads": set()}
                    DECLARATIONS.setdefault(owner, {}).setdefault(target.id, []).append(site)
                    NAMES.add(target.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = (*scope, node.name)
            for child in ast.iter_child_nodes(node):
                visit(child, scope)

        visit(tree)


def _meta_policy(meta):
    key = id(meta)
    if key in META_CACHE:
        return META_CACHE[key]
    result = (True, set())
    for method in ("__new__", "__init__", "__prepare__"):
        owner = next(base for base in type.__getattribute__(meta, "__mro__") if method in vars(base))
        if owner is type:
            continue
        fn = vars(owner)[method]
        if isinstance(fn, (staticmethod, classmethod)):
            fn = fn.__func__
        if method != "__new__" or not inspect.isfunction(fn) or not fn.__module__.startswith("sqlglot") or owner.__bases__ != (type,):
            result = (False, set())
            break
        try:
            import textwrap
            function = next(n for n in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(fn)))) if isinstance(n, ast.FunctionDef))
            namespace = (function.args.posonlyargs + function.args.args)[3].arg
            parents = {id(child): parent for parent in ast.walk(function) for child in ast.iter_child_nodes(parent)}
            blocked = set()
            for name in ast.walk(function):
                if not isinstance(name, ast.Name) or name.id != namespace or not isinstance(name.ctx, ast.Load):
                    continue
                parent = parents.get(id(name))
                if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute) and parent.func.attr == "__new__" and isinstance(parent.func.value, ast.Call) and isinstance(parent.func.value.func, ast.Name) and parent.func.value.func.id == "super":
                    continue
                if isinstance(parent, ast.Subscript) and isinstance(parent.slice, ast.Constant) and isinstance(parent.slice.value, str):
                    blocked.add(parent.slice.value)
                    continue
                call = parents.get(id(parent))
                if isinstance(parent, ast.Attribute) and isinstance(call, ast.Call) and parent.attr in ("get", "pop", "setdefault") and call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                    blocked.add(call.args[0].value)
                    continue
                raise ValueError("untracked namespace consumption")
            result = (True, result[1] | blocked)
        except (ValueError, TypeError, IndexError, SyntaxError, OSError, StopIteration):
            result = (False, set())
            break
    META_CACHE[key] = result
    return result


def register(owner):
    def decorate(cls):
        allowed, blocked = _meta_policy(type(cls))
        namespace = type.__getattribute__(cls, "__dict__")
        for name, sites in DECLARATIONS.get(owner, {}).items():
            for site in sites:
                data = FIELDS[site]
                actual = namespace.get(name)
                data["eligible"] = allowed and not data["decorated"] and name not in blocked and type(actual) is type(data["value"]) and actual == data["value"]
        return cls
    return decorate


def _record(site):
    context = "" if IMPORT_DEPTH else CURRENT()
    if context:
        FIELDS[site]["reads"].add(context)
    else:
        FIELDS[site]["global"] = True


def _read(obj, name):
    if name not in NAMES:
        return
    cls = obj if isinstance(obj, type) else type(obj)
    for base in type.__getattribute__(cls, "__mro__"):
        if name in type.__getattribute__(base, "__dict__"):
            sites = DECLARATIONS.get(_name(base), {}).get(name)
            if sites:
                for site in sites:
                    _record(site)
            else:
                UNKNOWN.setdefault(name, set()).add("" if IMPORT_DEPTH else CURRENT())
            return


def relevant(obj):
    return isinstance(obj, type) and any(_name(base) in DECLARATIONS for base in type.__getattribute__(obj, "__mro__"))


class Namespace(Mapping):
    def __init__(self, owner, value):
        self.owner, self.value = owner, value

    def __getitem__(self, key):
        result = self.value[key]
        if isinstance(key, str):
            _read(self.owner, key)
        return result

    def __iter__(self):
        return iter(self.value)

    def __len__(self):
        return len(self.value)

    def copy(self):
        return dict(self.items())


def value(obj, name):
    result = getattr(obj, name)
    if name == "__dict__" and relevant(obj):
        return Namespace(obj, result)
    _read(obj, name)
    return result


def dynamic(getter, obj, name, *default):
    result = getter(obj, name, *default)
    if getter is getattr and isinstance(name, str):
        if name == "__dict__" and relevant(obj):
            return Namespace(obj, result)
        _read(obj, name)
    return result


def namespace(getter, obj):
    result = getter(obj)
    return Namespace(obj, result) if getter is vars and relevant(obj) else result


def named(name, result):
    # A class body can read a setting through LOAD_NAME rather than an
    # attribute (for example DERIVED = FLAG). Keep all same-name declarations.
    UNKNOWN.setdefault(name, set()).add("" if IMPORT_DEPTH else CURRENT())
    return result


def export():
    result = {}
    for site, data in FIELDS.items():
        unknown = UNKNOWN.get(data["name"], set())
        result[site] = {"eligible": data["eligible"], "global": data["global"] or "" in unknown, "reads": sorted(data["reads"] | (unknown - {""}))}
    return result
