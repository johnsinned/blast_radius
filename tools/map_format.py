"""Encode sparse test sets compactly without changing their indices."""
import json


def decode_mask(value):
    return int(value, 16) if isinstance(value, str) else sum(1 << n for n in value)


def compact_masks(result):
    for i, value in enumerate(result["sets"]):
        mask = decode_mask(value)
        members = []
        rest = mask
        while rest:
            bit = rest & -rest
            members.append(bit.bit_length() - 1)
            rest ^= bit
        hexadecimal = format(mask, "x")
        result["sets"][i] = members if len(json.dumps(members, separators=(",", ":"))) < len(hexadecimal) + 2 else hexadecimal
