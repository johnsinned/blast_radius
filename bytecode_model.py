"""Pinned-interpreter instruction identities and conservative basic blocks."""
import bisect
import dis
import difflib
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


def aligned_changes(before, after):
    """Find old instructions at which two aligned control-flow graphs diverge.

    An alignment only proposes equivalent nodes. Every outgoing edge must also
    map to the exact corresponding successor. Insertions therefore select the
    old predecessor, including jumps into inserted regions; deletions select
    the removed nodes. A changed entry selects the original entry block.
    """
    if before.co_exceptiontable or after.co_exceptiontable or before.co_stacksize != after.co_stacksize:
        return None
    shape = lambda code: (code.co_flags, code.co_varnames, code.co_freevars, code.co_cellvars)
    if shape(before) != shape(after) or before.co_flags & (0x20 | 0x80 | 0x200):
        return None
    old = list(dis.get_instructions(before, adaptive=False))
    new = list(dis.get_instructions(after, adaptive=False))
    if not old or not new or max(len(old), len(new)) > 4000:
        return None
    jumps = set(getattr(dis, "hasjump", ())) | set(dis.hasjabs) | set(dis.hasjrel)

    def key(instruction):
        if instruction.opcode in jumps:
            return instruction.opname, "jump"
        if instruction.opcode in dis.hasconst:
            # Keep pool indices too: merging/reordering literals may alter
            # identity relationships even when one literal's value is equal.
            return instruction.opname, instruction.arg, constant(instruction.argval)
        if instruction.opcode in dis.hasname:
            # argrepr retains NULL/method flags while avoiding name-table indices.
            return instruction.opname, instruction.argval, instruction.argrepr
        return instruction.opname, instruction.arg, constant(instruction.argval)

    old_keys, new_keys = [key(i) for i in old], [key(i) for i in new]
    alignment = {}
    matcher = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)
    for a, b, size in matcher.get_matching_blocks():
        for i in range(size): alignment[old[a + i].offset] = new[b + i].offset

    def successors(instructions):
        result = {}
        for i, instruction in enumerate(instructions):
            edges = []
            if instruction.opcode in jumps:
                if type(instruction.argval) is not int:
                    raise ValueError("unsupported jump target")
                edges.append(("jump", instruction.argval))
            terminal = instruction.opname.startswith(("RETURN", "RAISE", "RERAISE")) or instruction.opname in ("JUMP_FORWARD", "JUMP_BACKWARD", "JUMP_ABSOLUTE", "JUMP_BACKWARD_NO_INTERRUPT")
            if not terminal and i + 1 < len(instructions):
                edges.append(("next", instructions[i + 1].offset))
            result[instruction.offset] = edges
        return result

    old_edges, new_edges = successors(old), successors(new)
    changed = {instruction.offset for instruction in old if instruction.offset not in alignment}
    for offset, target in alignment.items():
        expected = [(kind, alignment.get(successor)) for kind, successor in old_edges[offset]]
        if expected != new_edges[target]:
            changed.add(offset)
    if alignment.get(old[0].offset) != new[0].offset:
        changed.add(old[0].offset)
    return sorted(changed)
