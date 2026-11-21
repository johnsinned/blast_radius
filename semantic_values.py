"""Bounded, exact snapshots of primitive baseline values and pure comparisons."""
import math
import operator
import json

OPERATIONS = {
    "Eq": operator.eq, "NotEq": operator.ne, "Lt": operator.lt,
    "LtE": operator.le, "Gt": operator.gt, "GtE": operator.ge,
    "Is": operator.is_, "IsNot": operator.is_not,
    "In": lambda a, b: operator.contains(b, a),
    "NotIn": lambda a, b: not operator.contains(b, a),
}
BUILTIN_NONE_EQUALITY_TYPES = (bool, int, float, complex, str, bytes, bytearray, tuple, list, dict, set, frozenset, range)


def snapshot(value, budget=None, depth=0):
    """Never invoke an operand's conversion, equality, or iteration overrides."""
    if budget is None:
        budget = [256]
    budget[0] -= 1
    if budget[0] < 0 or depth > 4:
        raise ValueError("snapshot limit")
    cls = type(value)
    if value is None:
        return ("none",)
    if cls is bool:
        return ("bool", value)
    if cls is int and value.bit_length() <= 128:
        return ("int", value)
    if cls is float and math.isfinite(value):
        return ("float", value.hex())
    if cls is str and len(value) <= 128:
        budget[0] -= len(value)
        return ("str", value)
    if cls is bytes and len(value) <= 64:
        budget[0] -= len(value)
        return ("bytes", value.hex())
    if (cls is tuple or cls is list or cls is set or cls is frozenset) and len(value) <= 16:
        return (cls.__name__, tuple(snapshot(v, budget, depth + 1) for v in value))
    if cls is dict and len(value) <= 16:
        return ("dict", tuple((snapshot(k, budget, depth + 1), snapshot(v, budget, depth + 1)) for k, v in value.items()))
    raise ValueError("nonprimitive or oversized operand")


def restore(value):
    tag = value[0]
    if tag == "none":
        return None
    if tag in ("bool", "int", "str"):
        return value[1]
    if tag == "float":
        return float.fromhex(value[1])
    if tag == "bytes":
        return bytes.fromhex(value[1])
    if tag == "dict":
        return {restore(k): restore(v) for k, v in value[1]}
    constructors = {"list": list, "tuple": tuple, "set": set, "frozenset": frozenset}
    return constructors[tag](restore(v) for v in value[1])


def compare(operation, left, right, alias=None):
    if operation == "Is" and alias is not None:
        return alias
    if operation == "IsNot" and alias is not None:
        return not alias
    return OPERATIONS[operation](left, right)


def dictionary_key_id(key):
    cls = type(key)
    if key is None:
        value = ["none"]
    elif cls is str:
        value = ["str", key]
    elif cls is bytes:
        value = ["bytes", key.hex()]
    elif cls is bool or cls is int:
        value = ["number", str(int(key)), "1"]
    elif cls is float:
        try:
            n, d = key.as_integer_ratio()
        except (OverflowError, ValueError):
            return None
        value = ["number", str(n), str(d)]
    else:
        return None
    return json.dumps(value, separators=(",", ":"))
