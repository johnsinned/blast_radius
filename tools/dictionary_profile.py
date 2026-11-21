"""Observe reads of module/class dictionary literals in the pristine program."""
import json

CURRENT = lambda: ""
SITES = {}
READ_HOOK = None


def key_id(key):
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


class ObservedDict(dict):
    def __init__(self, site, value):
        super().__init__(value)
        self._blast_site = site

    def _read(self, key=None, whole=False):
        site = getattr(self, "_blast_site", None)
        if not site:
            return
        if READ_HOOK is not None:
            READ_HOOK(site, key, whole)
        record = SITES.setdefault(site, {"whole": set(), "keys": {}, "global_whole": False, "global_keys": set()})
        context = CURRENT()
        token = None if whole else key_id(key)
        if not context:
            if token is None:
                record["global_whole"] = True
            else:
                record["global_keys"].add(token)
        elif token is None:
            record["whole"].add(context)
        else:
            record["keys"].setdefault(token, set()).add(context)

    def __getitem__(self, key):
        self._read(key)
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        self._read(key)
        return dict.get(self, key, default)

    def setdefault(self, key, default=None):
        self._read(key)
        return dict.setdefault(self, key, default)

    def pop(self, key, *default):
        self._read(key)
        return dict.pop(self, key, *default)

    def popitem(self):
        self._read(whole=True)
        return dict.popitem(self)

    def __iter__(self):
        # Keys/shape do not depend on a value-only edit. Overriding iteration
        # also makes CPython's mapping-copy path consult keys and __getitem__.
        return dict.__iter__(self)

    def keys(self):
        return dict.keys(self)

    def items(self):
        self._read(whole=True)
        return dict.items(self)

    def values(self):
        self._read(whole=True)
        return dict.values(self)

    def copy(self):
        self._read(whole=True)
        return dict.copy(self)

    def __or__(self, other):
        self._read(whole=True)
        if isinstance(other, ObservedDict):
            other._read(whole=True)
        return dict.__or__(self, other)

    def __ror__(self, other):
        self._read(whole=True)
        return dict.__ror__(self, other)

    def __eq__(self, other):
        self._read(whole=True)
        if isinstance(other, ObservedDict):
            other._read(whole=True)
        return dict.__eq__(self, other)

    def __ne__(self, other):
        self._read(whole=True)
        if isinstance(other, ObservedDict):
            other._read(whole=True)
        return dict.__ne__(self, other)

    def __repr__(self):
        self._read(whole=True)
        return dict.__repr__(self)

    def __reduce_ex__(self, protocol):
        self._read(whole=True)
        return dict, (dict(self),)


def observe(site, value):
    SITES.setdefault(site, {"whole": set(), "keys": {}, "global_whole": False, "global_keys": set()})
    return ObservedDict(site, value)


def export():
    return {site: {"whole": sorted(data["whole"]), "keys": {key: sorted(contexts) for key, contexts in data["keys"].items()}, "global_whole": data["global_whole"], "global_keys": sorted(data["global_keys"])} for site, data in SITES.items()}
