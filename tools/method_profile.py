"""Conservative short-circuit proofs for original equality methods versus None."""
import ast
import builtins
import types

ORIGINAL = {}
SELF, NIL, SELF_TYPE, NIL_TYPE, UNKNOWN = (object() for _ in range(5))


def register(tree, code, path, namespace):
    methods = {(node.name, min([node.lineno] + [d.lineno for d in node.decorator_list])): node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in ("__eq__", "__ne__")}

    def walk(current):
        node = methods.get((current.co_name, current.co_firstlineno))
        if node:
            ORIGINAL[id(current)] = (current, node, path, namespace)
        for value in current.co_consts:
            if isinstance(value, types.CodeType):
                walk(value)
    walk(code)


def method(fn):
    if not isinstance(fn, types.FunctionType):
        return None
    record = ORIGINAL.get(id(fn.__code__))
    if not record or record[0] is not fn.__code__ or record[3] is not fn.__globals__:
        return None
    node = record[1]
    args = node.args
    positional = args.posonlyargs + args.args
    body = node.body[1:] if ast.get_docstring(node, clean=False) is not None else node.body
    if node.decorator_list or len(positional) != 2 or args.defaults or args.kwonlyargs or args.vararg or args.kwarg or "type" in fn.__code__.co_freevars or any(a.arg == "type" for a in positional) or len(body) != 1 or not isinstance(body[0], ast.Return):
        return None
    return record, positional[0].arg, positional[1].arg, body[0].value


def prove(cls, eq, ne):
    info = method(eq)
    if not info:
        return None
    record, receiver, other, expression = info
    namespace = record[3]
    if namespace.get("type", builtins.type) is not builtins.type or eq.__builtins__.get("type") is not builtins.type:
        return None

    def evaluate(node):
        if isinstance(node, ast.Name):
            return SELF if node.id == receiver else NIL if node.id == other else UNKNOWN
        if isinstance(node, ast.Constant) and (node.value is None or type(node.value) is bool):
            return NIL if node.value is None else node.value
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "type" and len(node.args) == 1 and not node.keywords:
            value = evaluate(node.args[0])
            return SELF_TYPE if value is SELF else NIL_TYPE if value is NIL else UNKNOWN
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.Is, ast.IsNot)):
            left, right = evaluate(node.left), evaluate(node.comparators[0])
            if left is UNKNOWN or right is UNKNOWN:
                return UNKNOWN
            return (left is right) if isinstance(node.ops[0], ast.Is) else (left is not right)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            value = evaluate(node.operand)
            return not value if type(value) is bool else UNKNOWN
        if isinstance(node, ast.BoolOp):
            for value in node.values:
                result = evaluate(value)
                if type(result) is not bool:
                    return UNKNOWN
                if isinstance(node.op, ast.Or) and result or isinstance(node.op, ast.And) and not result:
                    return result
            return result
        return UNKNOWN

    if evaluate(expression) is not False:
        return None
    ne_safe, paths = ne is object.__ne__, [record[2]]
    ne_info = method(ne)
    getattribute = next((type.__getattribute__(base, "__dict__")["__getattribute__"] for base in type.__getattribute__(cls, "__mro__") if "__getattribute__" in type.__getattribute__(base, "__dict__")), None)
    if ne_info and type.__getattribute__(cls, "__dictoffset__") == 0 and getattribute is object.__getattribute__:
        ne_record, a, b, body = ne_info
        expected = ast.parse(f"not {a}.__eq__({b})", mode="eval").body
        if ast.dump(body) == ast.dump(expected):
            ne_safe = True
            paths.append(ne_record[2])
    guards = [(namespace, "type", builtins.type), (eq.__builtins__, "type", builtins.type), (eq, "__code__", eq.__code__)]
    if ne_safe and isinstance(ne, types.FunctionType):
        guards.append((ne, "__code__", ne.__code__))
    return {"none_eq": True, "none_ne": ne_safe, "proof_files": sorted(set(paths))}, guards
