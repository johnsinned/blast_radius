"""Observe pristine basic-block execution without rewriting source or bytecode."""
from pathlib import Path
import sys
from bytecode_model import code_key, fingerprint, layout


class BlockCoverage:
    def __init__(self, root, current):
        self.root, self.current = str(root) + "/", current
        self.paths, self.cache, self.records = {}, {}, {}
        self.modules = []
        self.tool = 5
        self.invalid = False

    def data(self, code):
        key = id(code)
        if key in self.cache:
            return self.cache[key][1]
        filename = code.co_filename
        if filename not in self.paths:
            try:
                path = Path(filename).resolve().relative_to(Path(self.root)).as_posix()
            except (ValueError, OSError):
                path = None
            self.paths[filename] = path if path and path.startswith("sqlglot/") and path.endswith(".py") else None
        path = self.paths[filename]
        data = None
        if path:
            _, offsets = layout(code)
            identity = path + ":" + code_key(code)
            signature = fingerprint(code)
            if identity in self.records:
                # Repeated imports and same-line lambdas can share a source ID.
                # Their observations can be merged only for identical code.
                data = self.records[identity]
                if data["fingerprint"] != signature:
                    data["ambiguous"] = True
            else:
                data = {"fingerprint": signature, "ambiguous": False, "offsets": offsets, "blocks": {offset: {"global": False, "contexts": set()} for offset in set(offsets.values())}}
                self.records[identity] = data
        self.cache[key] = (code, data)
        return data

    def record(self, code, offset):
        data = self.data(code)
        if data:
            block = data["blocks"].get(data["offsets"].get(offset))
            if block is None:
                data["ambiguous"] = True
            elif self.modules or not self.current():
                block["global"] = True
            else:
                block["contexts"].add(self.current())
        return sys.monitoring.DISABLE

    def enter(self, code, offset):
        data = self.data(code)
        if not data:
            return sys.monitoring.DISABLE
        events = sys.monitoring.events
        sys.monitoring.set_local_events(self.tool, code, events.INSTRUCTION | events.PY_RETURN | events.PY_YIELD | events.PY_RESUME)
        if code.co_name == "<module>":
            self.modules.append(code)
            sys.monitoring.restart_events()
        self.record(code, offset)
        return None if code.co_name == "<module>" else sys.monitoring.DISABLE

    def leave(self, code, offset, value):
        self.record(code, offset)
        if code.co_name == "<module>" and self.data(code):
            if not self.modules or self.modules[-1] is not code:
                self.invalid = True
            else:
                self.modules.pop()
            sys.monitoring.restart_events()
            return None
        return sys.monitoring.DISABLE

    def unwind(self, code, offset, exception):
        # This is a non-disableable event. It also closes module execution if
        # an intentional import failure occurs during a test.
        self.record(code, offset)
        if code.co_name == "<module>" and self.data(code):
            if self.modules and self.modules[-1] is code:
                self.modules.pop()
                sys.monitoring.restart_events()
            else:
                self.invalid = True

    def throwing(self, code, offset, exception):
        self.record(code, offset)

    def start(self):
        monitor, events = sys.monitoring, sys.monitoring.events
        monitor.use_tool_id(self.tool, "blast-radius-blocks")
        for event, callback in ((events.PY_START, self.enter), (events.INSTRUCTION, self.record), (events.PY_RETURN, self.leave), (events.PY_YIELD, lambda code, offset, value: self.record(code, offset)), (events.PY_RESUME, self.record), (events.PY_THROW, self.throwing), (events.PY_UNWIND, self.unwind)):
            monitor.register_callback(self.tool, event, callback)
        monitor.set_events(self.tool, events.PY_START | events.PY_UNWIND | events.PY_THROW)

    def switch_context(self, context):
        sys.monitoring.restart_events()

    def stop(self):
        monitor = sys.monitoring
        monitor.set_events(self.tool, 0)
        for code, data in self.cache.values():
            if data:
                monitor.set_local_events(self.tool, code, 0)
        for event in (monitor.events.PY_START, monitor.events.INSTRUCTION, monitor.events.PY_RETURN, monitor.events.PY_YIELD, monitor.events.PY_RESUME, monitor.events.PY_THROW, monitor.events.PY_UNWIND):
            monitor.register_callback(self.tool, event, None)
        monitor.free_tool_id(self.tool)

    def export(self):
        return {"python": list(sys.version_info[:2]), "valid": not self.invalid and not self.modules, "codes": {key: {"fingerprint": data["fingerprint"], "ambiguous": data["ambiguous"], "blocks": {str(offset): {"global": block["global"], "contexts": sorted(block["contexts"])} for offset, block in data["blocks"].items()}} for key, data in self.records.items()}}

    def save(self): pass
    def get_data(self): return self
    def measured_files(self): return []
