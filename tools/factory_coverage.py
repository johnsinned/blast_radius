"""Track users of closures made by source-proven, side-effect-free factories."""
import ast
import bisect
import dis
from pathlib import Path
import sys
import types
from bytecode_model import code_key, fingerprint, walk_codes


class FactoryCoverage:
    def __init__(self, root, current):
        self.root, self.current, self.tool = root, current, 5
        self.cache, self.proofs, self.children = {}, {}, set()
        self.sites, self.environments, self.unknown = {}, {}, {}
        self.functions, self.modules = [], []
        self.invalid = False
        for path in sorted((root / "sqlglot").rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_bytes())
            candidates = {}
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef): continue
                body = node.body[1:] if ast.get_docstring(node, clean=False) is not None else node.body
                if len(body) != 2 or not isinstance(body[0], ast.FunctionDef) or not isinstance(body[1], ast.Return) or not isinstance(body[1].value, ast.Name) or body[1].value.id != body[0].name: continue
                nested = body[0]
                if nested.decorator_list: continue
                parameters = {arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs, *([node.args.vararg] if node.args.vararg else []), *([node.args.kwarg] if node.args.kwarg else [])]}
                header = [value for key, value in ast.iter_fields(nested) if key != "body"]
                header_nodes = [item for value in header for item in (value if isinstance(value, list) else [value]) if isinstance(item, ast.AST)]
                if any(isinstance(n, ast.Name) and n.id in parameters for h in header_nodes for n in ast.walk(h)): continue
                candidates[(node.name, min([node.lineno] + [d.lineno for d in node.decorator_list]))] = (node, nested)
            for code in walk_codes(compile(path.read_bytes(), str(path), "exec", dont_inherit=True)):
                pair = candidates.get((code.co_name, code.co_firstlineno))
                if pair:
                    node, nested = pair
                    key = relative + ":" + code_key(code)
                    self.proofs[key] = {"fingerprint": fingerprint(code), "path": relative, "span": [node.lineno, node.end_lineno]}
                    for child in code.co_consts:
                        if isinstance(child, types.CodeType) and child.co_name == nested.name:
                            self.children.add(relative + ":" + code_key(child))

    def info(self, code):
        if id(code) not in self.cache:
            try:
                path = Path(code.co_filename).resolve().relative_to(self.root).as_posix()
            except (ValueError, OSError):
                path = ""
            info = None
            if path.startswith("sqlglot/") and path.endswith(".py"):
                instructions = list(dis.get_instructions(code, adaptive=False))
                info = {"key": path + ":" + code_key(code), "path": path, "instructions": instructions, "offsets": [i.offset for i in instructions]}
            self.cache[id(code)] = (code, info)
        return self.cache[id(code)][1]

    def site(self, code, offset):
        info = self.info(code)
        if not info: return None
        index = bisect.bisect_right(info["offsets"], offset) - 1
        if index < 0: return None
        instruction = info["instructions"][index]
        p = instruction.positions
        if not instruction.opname.startswith("CALL") or p.lineno is None or p.col_offset is None: return None
        return f"{info['path']}:{p.lineno}:{p.col_offset}"

    def calling(self, code, offset, callable, arg0):
        site = self.site(code, offset)
        if not site: return
        proof = None
        if type(callable) is types.FunctionType:
            info = self.info(callable.__code__)
            proof = self.proofs.get(info["key"]) if info else None
            if proof and fingerprint(callable.__code__) != proof["fingerprint"]: proof = None
        record = self.sites.setdefault(site, {"eligible": True, "proof": proof, "users": set(), "global": False, "calls": 0, "returns": 0})
        record["calls"] += 1
        if not proof or not self.modules or record["proof"] != proof:
            record["eligible"] = False

    def frame(self, code):
        frame = sys._getframe(1)
        while frame and frame.f_code is not code: frame = frame.f_back
        return frame

    def enter(self, code, offset):
        info = self.info(code)
        if not info: return sys.monitoring.DISABLE
        events = sys.monitoring.events
        if not code.co_flags & 2:
            sys.monitoring.set_local_events(self.tool, code, events.CALL | events.PY_RETURN)
            if code.co_name == "<module>": self.modules.append(code)
        if info["key"] in self.proofs:
            sys.monitoring.set_local_events(self.tool, code, events.PY_RETURN)
        if info["key"] in self.children:
            frame = self.frame(code)
            environment = tuple(id(frame.f_locals[name]) for name in code.co_freevars) if frame else None
            sites = self.environments.get((info["key"], environment))
            context = self.current() if not self.modules else ""
            if sites:
                for site in sites:
                    if context: self.sites[site]["users"].add(context)
                    else: self.sites[site]["global"] = True
            else:
                self.unknown.setdefault(info["key"], set()).add(context)
            return None
        return None if code.co_name == "<module>" else sys.monitoring.DISABLE

    def leave(self, code, offset, value):
        info = self.info(code)
        if not info: return sys.monitoring.DISABLE
        if info["key"] in self.proofs:
            frame = self.frame(code)
            caller = frame.f_back if frame else None
            site = self.site(caller.f_code, caller.f_lasti) if caller else None
            record = self.sites.get(site)
            if record and record["eligible"]:
                record["returns"] += 1
                if type(value) is not types.FunctionType or self.info(value.__code__) is None or self.info(value.__code__)["key"] not in self.children:
                    record["eligible"] = False
                else:
                    key = self.info(value.__code__)["key"]
                    environment = tuple(id(cell.cell_contents) for cell in value.__closure__ or ())
                    self.environments.setdefault((key, environment), set()).add(site)
                    record.setdefault("codes", set()).add(key)
                    self.functions.append(value)
        if code.co_name == "<module>":
            if not self.modules or self.modules[-1] is not code: self.invalid = True
            else: self.modules.pop()
            return None
        return None if info["key"] in self.proofs else sys.monitoring.DISABLE

    def unwind(self, code, offset, exception):
        info = self.info(code)
        if info and code.co_name == "<module>":
            if self.modules and self.modules[-1] is code: self.modules.pop()
            else: self.invalid = True

    def start(self):
        m, e = sys.monitoring, sys.monitoring.events
        m.use_tool_id(self.tool, "blast-radius-factories")
        for event, callback in ((e.PY_START, self.enter), (e.PY_RETURN, self.leave), (e.CALL, self.calling), (e.PY_UNWIND, self.unwind)):
            m.register_callback(self.tool, event, callback)
        m.set_events(self.tool, e.PY_START | e.PY_UNWIND)

    def stop(self):
        m = sys.monitoring
        m.set_events(self.tool, 0)
        for code, info in self.cache.values():
            if info: m.set_local_events(self.tool, code, 0)
        for event in (m.events.PY_START, m.events.PY_RETURN, m.events.CALL, m.events.PY_UNWIND): m.register_callback(self.tool, event, None)
        m.free_tool_id(self.tool)

    def export(self):
        result = {}
        for site, data in self.sites.items():
            if not data["proof"]: continue
            unknown = {context for key in data.get("codes", []) for context in self.unknown.get(key, [])}
            complete = bool(data.get("codes")) and data["calls"] == data["returns"]
            result[site] = {"eligible": data["eligible"] and complete, "global": data["global"] or "" in unknown, "users": sorted(data["users"] | (unknown - {""})), "proof": data["proof"]}
        return {"valid": not self.invalid and not self.modules, "sites": result}

    def switch_context(self, context): sys.monitoring.restart_events()
    def save(self): pass
    def get_data(self): return self
    def measured_files(self): return []
