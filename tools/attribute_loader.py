"""Instrument baseline setting reads, including reads made by the tests."""
import ast
import importlib.abc
import importlib.machinery
from pathlib import Path
import sys
try:
    from . import attribute_profile as profile
except ImportError:
    import attribute_profile as profile

VALUE = "__blast_setting_value__"
DYNAMIC = "__blast_setting_getattr__"
NAMESPACE = "__blast_setting_vars__"
REGISTER = "__blast_setting_register__"
NAMED = "__blast_setting_name__"


class Instrument(ast.NodeTransformer):
    def __init__(self, module):
        self.module, self.scope = module, []

    def visit_ClassDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        owner = self.module + ":" + ".".join(self.scope)
        self.scope.pop()
        if owner in profile.DECLARATIONS:
            node.decorator_list.append(ast.Call(func=ast.Name(id=REGISTER, ctx=ast.Load()), args=[ast.Constant(owner)], keywords=[]))
        return node

    def visit_FunctionDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Attribute(self, node):
        self.generic_visit(node)
        if isinstance(node.ctx, ast.Load) and (node.attr in profile.NAMES or node.attr == "__dict__"):
            return ast.copy_location(ast.Call(func=ast.Name(id=VALUE, ctx=ast.Load()), args=[node.value, ast.Constant(node.attr)], keywords=[]), node)
        return node

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and node.id in profile.NAMES:
            return ast.copy_location(ast.Call(func=ast.Name(id=NAMED, ctx=ast.Load()), args=[ast.Constant(node.id), node], keywords=[]), node)
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and not node.keywords:
            helper = DYNAMIC if node.func.id == "getattr" and len(node.args) in (2, 3) else NAMESPACE if node.func.id == "vars" and len(node.args) == 1 else None
            if helper:
                return ast.copy_location(ast.Call(func=ast.Name(id=helper, ctx=ast.Load()), args=[node.func, *node.args], keywords=[]), node)
        return node


class Loader(importlib.abc.Loader):
    def __init__(self, original, filename):
        self.original, self.filename = original, filename

    def create_module(self, spec):
        return self.original.create_module(spec)

    def get_source(self, fullname):
        return self.original.get_source(fullname)

    def get_filename(self, fullname):
        return self.filename

    def exec_module(self, module):
        tree = Instrument(module.__name__).visit(ast.parse(self.get_source(module.__name__), filename=self.filename))
        ast.fix_missing_locations(tree)
        module.__dict__.update({VALUE: profile.value, DYNAMIC: profile.dynamic, NAMESPACE: profile.namespace, REGISTER: profile.register, NAMED: profile.named})
        profile.IMPORT_DEPTH += 1
        try:
            exec(compile(tree, self.filename, "exec", dont_inherit=True), module.__dict__)
        finally:
            profile.IMPORT_DEPTH -= 1


class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] not in ("sqlglot", "tests"):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and spec.origin and spec.origin.endswith(".py"):
            spec.loader = Loader(spec.loader, spec.origin)
        return spec


def install(root, current):
    profile.CURRENT = current
    profile.index(root)
    sys.meta_path.insert(0, Finder())
