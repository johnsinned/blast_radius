"""Pinned-interpreter instruction identities and conservative basic blocks."""
import bisect
import dis
import hashlib
import marshal
import types


def constant(value):
    if isinstance(value, types.CodeType):
        return ("code", value.co_qualname, value.co_flags, value.co_varnames, value.co_freevars, value.co_cellvars)
    if isinstance(value, tuple):
        return ("tuple", tuple(constant(item) for item in value))
    return ("literal", marshal.dumps(value))


def fingerprint(code):
    data = (code.co_code, tuple(constant(value) for value in code.co_consts), code.co_names, code.co_varnames, code.co_freevars, code.co_cellvars, code.co_flags, code.co_exceptiontable, code.co_stacksize)
    return hashlib.sha256(marshal.dumps(data)).hexdigest()


def code_key(code):
    return str(code.co_firstlineno) + ":" + code.co_qualname


def layout(code):
    instructions = list(dis.get_instructions(code, show_caches=True, adaptive=False))
    if not instructions:
        raise ValueError("code contains no instructions")
    jumps = set(getattr(dis, "hasjump", ())) | set(dis.hasjabs) | set(dis.hasjrel)
    leaders = {instructions[0].offset}
    for i, instruction in enumerate(instructions):
        if instruction.opcode in jumps:
            if type(instruction.argval) is not int:
                raise ValueError("unrecognized jump target")
            leaders.add(instruction.argval)
        if instruction.opcode in jumps or instruction.opname.startswith(("RETURN", "RAISE", "RERAISE")):
            if i + 1 < len(instructions):
                leaders.add(instructions[i + 1].offset)
    for entry in dis.Bytecode(code).exception_entries:
        leaders.add(entry.target)
    leaders = sorted(leaders)
    blocks = {instruction.offset: leaders[bisect.bisect_right(leaders, instruction.offset) - 1] for instruction in instructions}
    return instructions, blocks


def walk_codes(code):
    yield code
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            yield from walk_codes(value)


def changed_offsets(before, after):
    if len(before.co_code) != len(after.co_code) or before.co_exceptiontable != after.co_exceptiontable or before.co_stacksize != after.co_stacksize:
        return None
    old, _ = layout(before)
    new, _ = layout(after)
    if [i.offset for i in old] != [i.offset for i in new]:
        return None
    return [a.offset for a, b in zip(old, new) if (a.opname, a.arg, constant(a.argval)) != (b.opname, b.arg, constant(b.argval))]
