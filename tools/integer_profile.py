"""Record enclosing integer ranges of original operands, never mutant outputs."""
import ast
from semantic_values import OPERATIONS

CURRENT = lambda: ""
SITES = {}


def literal(node):
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return None
    return value if type(value) is int and value.bit_length() <= 128 else None


def supported(node):
    return len(node.ops) == 1 and isinstance(node.ops[0], (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)) and any(literal(value) is not None for value in (node.left, node.comparators[0]))


def probe(site, operation, left, right):
    context = CURRENT()
    data = SITES.setdefault(site, {"global": False, "ranges": {}, "unknown": set()})
    if not context:
        data["global"] = True
    elif type(left) is int and type(right) is int and left.bit_length() <= 128 and right.bit_length() <= 128:
        bounds = data["ranges"].get(context)
        if bounds is None:
            data["ranges"][context] = [left, left, right, right]
        else:
            if left < bounds[0]: bounds[0] = left
            if left > bounds[1]: bounds[1] = left
            if right < bounds[2]: bounds[2] = right
            if right > bounds[3]: bounds[3] = right
    else:
        data["unknown"].add(context)
    return OPERATIONS[operation](left, right)


def export():
    return {site: {"global": data["global"], "ranges": data["ranges"], "unknown": sorted(data["unknown"])} for site, data in SITES.items()}
