"""Profile inputs to the pinned expression required-argument validator.

Only initializer reads are transparent. Copies/spreads and every other value
consumer remain dependencies, so this does not need to infer alias propagation.
"""
import ast
from collections import defaultdict
import sys

try:
    from . import comparison_profile, dictionary_profile
except ImportError:
    import comparison_profile
    import dictionary_profile
from bytecode_model import fingerprint, walk_codes


class RequirementCoverage:
    def __init__(self, root, current):
        self.root, self.current = root, current
        self.tool = 5
        self.context = ""
        self.sites, self.unsafe, self.global_keys = {}, defaultdict(lambda: defaultdict(set)), defaultdict(set)
        self.raw, self.global_raw, self.unknown = defaultdict(set), set(), set()
        self.cache = {}
        self.failures = defaultdict(int)
        path = root / "sqlglot/expressions/core.py"
        tree = ast.parse(path.read_text())
        codes = {c.co_qualname: c for c in walk_codes(compile(tree, str(path), "exec", dont_inherit=True))}
        self.initializer = fingerprint(codes["Expr.__init_subclass__"])
        self.validator = fingerprint(codes["Expression.error_messages"])
        self.proof = {"proof_files": ["sqlglot/expressions/core.py"], "proof_spans": {"sqlglot/expressions/core.py": [[n.lineno, n.end_lineno] for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in ("__init_subclass__", "error_messages")]}}
        # Verify the derived set has exactly the two known direct consumers.
        uses = []
        for file in (*root.joinpath("sqlglot").rglob("*.py"), *root.joinpath("tests").rglob("*.py")):
            relative = file.relative_to(root).as_posix()
            parsed = ast.parse(file.read_text())
            for node in ast.walk(parsed):
                if isinstance(node, ast.Attribute) and node.attr == "required_args":
                    uses.append((relative, node.lineno, type(node.ctx).__name__))
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "arg_types" for t in node.targets) or isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "arg_types":
                    value = node.value
                    if isinstance(value, ast.Dict):
                        keys = [k.value for k, v in zip(value.keys, value.values) if isinstance(k, ast.Constant) and type(k.value) is str and isinstance(v, ast.Constant) and type(v.value) is bool]
                        if keys:
                            self.sites[f"{relative}:{value.lineno}:{value.col_offset}"] = keys
        expected = [("sqlglot/expressions/core.py", 110, "Store"), ("sqlglot/expressions/core.py", 1322, "Load")]
        if sorted(uses) != expected:
            raise ValueError("required_args consumers differ from the pinned proof")

    def identity(self, code):
        if code not in self.cache:
            name = code.co_qualname
            self.cache[code] = ("initializer" if name == "Expr.__init_subclass__" and fingerprint(code) == self.initializer else "validator" if name == "Expression.error_messages" and fingerprint(code) == self.validator else None)
        return self.cache[code]

    def read(self, site, key, whole):
        if site not in self.sites:
            return
        frame = sys._getframe(1)
        # Skip just the dictionary probe's own frames.
        while frame is not None and frame.f_code.co_filename == dictionary_profile.__file__:
            frame = frame.f_back
        if frame is not None and self.identity(frame.f_code) == "initializer":
            return
        context = "" if comparison_profile.IMPORT_DEPTH else self.current()
        if context:
            self.raw[site].add(context)
        else:
            self.global_raw.add(site)

    def record(self, code, offset):
        if self.identity(code) != "validator":
            return sys.monitoring.DISABLE
        frame = sys._getframe(1)
        while frame is not None and frame.f_code is not code:
            frame = frame.f_back
        context = "" if comparison_profile.IMPORT_DEPTH else self.current()
        site = None
        try:
            node = frame.f_locals["self"]
            cls = type(node)
            # Read exactly the metadata the validator will use, including an
            # instance override; unsupported overrides are dependencies.
            namespace = object.__getattribute__(node, "__dict__")
            if type(namespace) is not dict:
                raise ValueError("custom instance namespace")
            mapping = type.__getattribute__(cls, "arg_types")
            mapping = namespace.get("arg_types", mapping)
            if type(mapping) is dictionary_profile.ObservedDict:
                site = mapping._blast_site
                if site not in self.sites:
                    return
            elif type(mapping) is dict:
                # This literal was made in a function or outside the SUT.
                # A value-copy from an observed source is already a raw read.
                return
            lookup = next(base.__dict__["__getattribute__"] for base in cls.__mro__ if "__getattribute__" in base.__dict__)
            if lookup is not object.__getattribute__:
                raise ValueError("custom attribute lookup")
            if type(namespace) is not dict or "arg_types" in namespace or "required_args" in namespace:
                raise ValueError("instance-shadowed validation metadata")
            arguments = object.__getattribute__(node, "args")
            required = type.__getattribute__(cls, "required_args")
            if type(arguments) is not dict or type(mapping) is not dictionary_profile.ObservedDict or type(required) is not set:
                raise ValueError("custom argument storage")
            if any(type(k) is not str for k in arguments) or any(type(k) is not str or type(v) is not bool for k, v in dict.items(mapping)) or required != {k for k, v in dict.items(mapping) if v}:
                raise ValueError("modified validation metadata")
            missing = {}
            for key in dict.keys(mapping):
                value = dict.get(arguments, key)
                if value is None:
                    missing[key] = True
                elif type(value) is list:
                    missing[key] = not value
                elif any(base is list for base in type(value).__mro__) or next(base.__dict__["__class__"] for base in type(value).__mro__ if "__class__" in base.__dict__) is not object.__dict__["__class__"]:
                    raise ValueError("custom list/instance check")
                else:
                    missing[key] = False
            count = sum(missing[k] for k in required)
            for key in self.sites[site]:
                # Even a present argument can change ordering of multiple
                # existing errors when the required-key set is rebuilt.
                if missing.get(key, True) or count >= 2:
                    if context: self.unsafe[site][key].add(context)
                    else: self.global_keys[site].add(key)
        except (AttributeError, KeyError, StopIteration, TypeError, ValueError) as error:
            self.failures[(str(error), bool(context))] += 1
            if site in self.sites:
                if context: self.raw[site].add(context)
                else: self.global_raw.add(site)
            elif context: self.unknown.add(context)
            else: self.global_raw.update(self.sites)

    def start(self):
        dictionary_profile.READ_HOOK = self.read
        sys.monitoring.use_tool_id(self.tool, "blast-required-arguments")
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_START, self.record)
        sys.monitoring.set_events(self.tool, sys.monitoring.events.PY_START)

    def switch_context(self, context):
        self.context = context
        sys.monitoring.restart_events()

    def stop(self):
        dictionary_profile.READ_HOOK = None
        sys.monitoring.set_events(self.tool, 0)
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_START, None)
        sys.monitoring.free_tool_id(self.tool)

    def save(self): pass
    def get_data(self): return self
    def measured_files(self): return []

    def export(self):
        return {"failures": [[reason, context, count] for (reason, context), count in self.failures.items()], "proof": self.proof, "unknown": sorted(self.unknown), "sites": {site: {"raw": sorted(self.raw[site]), "global": site in self.global_raw, "global_keys": sorted(self.global_keys[site]), "keys": {key: sorted(self.unsafe[site][key]) for key in keys}} for site, keys in self.sites.items() if site.startswith("sqlglot/") and site in dictionary_profile.SITES}}
