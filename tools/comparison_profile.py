"""Record original comparison operands, without applying or evaluating mutants."""
import ast
import enum
import importlib.abc
import importlib.machinery
from pathlib import Path
import sys
import os
from semantic_values import OPERATIONS, snapshot, BUILTIN_NONE_EQUALITY_TYPES
try:
    from . import dictionary_profile
except ImportError:
    import dictionary_profile

CURRENT = lambda: ""
SITES = {}
TYPES = {}
TYPE_CACHE = {}
HELPER = "__blast_original_identity_comparison__"
DICT_HELPER = "__blast_original_dictionary__"


def describe(value):
    cls = type(value)
    key = id(cls)
    if key not in TYPE_CACHE:
        # Static MRO dictionaries avoid invoking operand descriptors/equality.
        mro = type.__getattribute__(cls, "__mro__")
        dictionaries = [type.__getattribute__(base, "__dict__") for base in mro]
        eq = next((d["__eq__"] for d in dictionaries if "__eq__" in d), None)
        ne = next((d["__ne__"] for d in dictionaries if "__ne__" in d), None)
        name = type.__getattribute__(cls, "__module__") + ":" + type.__getattribute__(cls, "__qualname__")
        kind = "unsupported"
        canonical = None
        if cls is type(None) or (eq is object.__eq__ and ne is object.__ne__):
            kind = "identity"
        elif any(cls is builtin for builtin in BUILTIN_NONE_EQUALITY_TYPES):
            kind = "builtin_value"
        elif type(cls) is enum.EnumType and eq is str.__eq__ and ne is str.__ne__:
            members = list({id(v): v for v in cls.__members__.values()}.values())
            # Canonical enum members must have distinct underlying str payloads.
            if len({str.__str__(v) for v in members}) == len(members):
                kind = "canonical_str_enum"
                canonical = {id(v) for v in members}
        description = {"name": name, "kind": kind}
        # Different definitions during tests are kept as distinct type states.
        signature = str(len(TYPE_CACHE)) + ":" + name + ":" + kind
        TYPES[signature] = description
        checks = [(d, d.get("__eq__"), d.get("__ne__")) for d in dictionaries]
        if cls is type(None) or cls is object or kind in ("unsupported", "builtin_value"):
            checks = []
        TYPE_CACHE[key] = signature, canonical, mro, checks
    signature, canonical, mro, checks = TYPE_CACHE[key]
    if checks and (type.__getattribute__(cls, "__mro__") is not mro or any(d.get("__eq__") is not eq or d.get("__ne__") is not ne for d, eq, ne in checks)):
        signature += ":modified"
        TYPES[signature] = {"name": TYPES[signature.removesuffix(":modified")]["name"], "kind": "unsupported"}
        return signature
    if canonical is not None and id(value) not in canonical:
        signature += ":noncanonical"
        TYPES[signature] = {"name": TYPES[signature.removesuffix(":noncanonical")]["name"], "kind": "unsupported"}
    return signature


def probe(site, operation, left, right):
    context = CURRENT()
    record = SITES.setdefault(site, {"global": False, "pairs": {}, "values": {}, "unknown": set()})
    if context:
        pair = (describe(left), describe(right))
        record["pairs"].setdefault(pair, set()).add(context)
        if context not in record["unknown"]:
            try:
                values = (snapshot(left), snapshot(right), left is right)
                if values not in record["values"] and len(record["values"]) >= 32:
                    raise ValueError("site snapshot limit")
                record["values"].setdefault(values, set()).add(context)
            except (ValueError, RecursionError):
                record["unknown"].add(context)
    else:
        record["global"] = True
    return OPERATIONS[operation](left, right)


class Instrument(ast.NodeTransformer):
    def __init__(self, relative):
        self.relative = relative
        self.function_depth = 0

    def visit_FunctionDef(self, node):
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1
        return node

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_Lambda = visit_FunctionDef

    def visit_Dict(self, node):
        self.generic_visit(node)
        if self.function_depth:
            return node
        site = f"{self.relative}:{node.lineno}:{node.col_offset}"
        return ast.copy_location(ast.Call(func=ast.Name(id=DICT_HELPER, ctx=ast.Load()), args=[ast.Constant(site), node], keywords=[]), node)

    def visit_Compare(self, node):
        self.generic_visit(node)
        if os.environ.get("BLAST_READS_ONLY") == "1":
            return node
        if len(node.ops) != 1:
            return node
        site = f"{self.relative}:{node.lineno}:{node.col_offset}"
        operation = type(node.ops[0]).__name__
        return ast.copy_location(ast.Call(func=ast.Name(id=HELPER, ctx=ast.Load()), args=[ast.Constant(site), ast.Constant(operation), node.left, node.comparators[0]], keywords=[]), node)


class Loader(importlib.abc.Loader):
    def __init__(self, original, filename, root):
        self.original, self.filename, self.root = original, filename, root

    def create_module(self, spec):
        return self.original.create_module(spec)

    def get_source(self, fullname):
        return self.original.get_source(fullname)

    def get_filename(self, fullname):
        return self.filename

    def exec_module(self, module):
        source = self.get_source(module.__name__)
        relative = Path(self.filename).resolve().relative_to(self.root).as_posix()
        tree = Instrument(relative).visit(ast.parse(source, filename=self.filename))
        ast.fix_missing_locations(tree)
        module.__dict__[HELPER] = probe
        module.__dict__[DICT_HELPER] = dictionary_profile.observe
        exec(compile(tree, self.filename, "exec", dont_inherit=True), module.__dict__)


class Finder(importlib.abc.MetaPathFinder):
    def __init__(self, root):
        self.root = root

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "sqlglot" and not fullname.startswith("sqlglot."):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and spec.origin and spec.origin.endswith(".py"):
            spec.loader = Loader(spec.loader, spec.origin, self.root)
        return spec


def install(root, current):
    global CURRENT
    CURRENT = current
    dictionary_profile.CURRENT = current
    if any(name == "sqlglot" or name.startswith("sqlglot.") for name in sys.modules):
        raise RuntimeError("identity profiles require instrumentation before importing sqlglot")
    sys.meta_path.insert(0, Finder(root))


def export():
    return {"types": TYPES, "sites": {site: {"global": value["global"], "pairs": [[left, right, sorted(contexts)] for (left, right), contexts in value["pairs"].items()], "values": [[a, b, alias, sorted(contexts)] for (a, b, alias), contexts in value["values"].items()], "unknown": sorted(value["unknown"])} for site, value in SITES.items()}}


def is_import_read():
    """Exclude loader source reads while retaining inspect/linecache reads."""
    frame = sys._getframe(1)
    for _ in range(12):
        if frame is None:
            return False
        if frame.f_code is Loader.get_source.__code__:
            return frame.f_back is not None and frame.f_back.f_code is Loader.exec_module.__code__
        frame = frame.f_back
    return False
