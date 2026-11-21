"""Collect only dependencies of the pristine checkout; never read patch answers."""
import json
import os
from pathlib import Path
import sys
import concurrent.futures
import functools
import inspect
from baseline_executor import TracedExecutor

import coverage
import pytest
import comparison_profile
import dictionary_profile
import attribute_loader
import attribute_profile

ROOT = Path(os.environ["BLAST_SOURCE"]).resolve()
OUT = Path(os.environ["BLAST_RAW_MAP"]).resolve()
CURRENT = ""
FILES = {}
CLASSES = {}
REPORTS = {}
TESTS = []
EXECUTED = set()
CLASS_SETUP = {}
class ReadOnlyCoverage:
    def start(self): pass
    def stop(self): pass
    def save(self): pass
    def switch_context(self, context): pass
    def get_data(self): return self
    def measured_files(self): return []


class MonitoringCoverage:
    """Record each source line once per context using Python 3.12+ monitoring."""
    def __init__(self):
        self.context = ""
        self.lines = {}
        self.paths = {}
        self.tool = 5
        self.global_functions = set()

    def record(self, code, lineno):
        filename = code.co_filename
        if filename not in self.paths:
            name = relative(filename)
            self.paths[filename] = name if name and name.startswith(("sqlglot/", "tests/")) else None
        if self.paths[filename] and self.context:
            self.lines.setdefault(filename, {}).setdefault(lineno, set()).add(self.context)
        return sys.monitoring.DISABLE

    def start(self):
        sys.monitoring.use_tool_id(self.tool, "blast-radius-baseline")
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.LINE, self.record)
        # One-line lambdas may emit no LINE event: their first instruction has
        # the same line as code.co_firstlineno. Function entry closes that gap.
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_START, self.start_code)
        sys.monitoring.set_events(self.tool, sys.monitoring.events.LINE | sys.monitoring.events.PY_START)

    def start_code(self, code, offset):
        result = self.record(code, code.co_firstlineno)
        path = self.paths.get(code.co_filename)
        if path and not self.context and code.co_flags & 2:
            self.global_functions.add(path + "::" + code.co_qualname.replace(".<locals>.", "."))
        return result

    def switch_context(self, context):
        self.context = context
        sys.monitoring.restart_events()

    def stop(self):
        sys.monitoring.set_events(self.tool, 0)
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.LINE, None)
        sys.monitoring.register_callback(self.tool, sys.monitoring.events.PY_START, None)
        sys.monitoring.free_tool_id(self.tool)

    def save(self):
        pass

    def get_data(self):
        return self

    def measured_files(self):
        return self.lines

    def contexts_by_lineno(self, filename):
        return {n: sorted(contexts) for n, contexts in self.lines[filename].items()}


COV = MonitoringCoverage() if hasattr(sys, "monitoring") and os.environ.get("BLAST_TRACER") != "coverage" else coverage.Coverage(data_file=str(OUT.with_suffix(".coverage")), source=[str(ROOT / "sqlglot"), str(ROOT / "tests")], config_file=False)
if any(os.environ.get(name) == "1" for name in ("BLAST_READS_ONLY", "BLAST_ATTRIBUTES_ONLY", "BLAST_TYPES_ONLY")):
    COV = ReadOnlyCoverage()


def relative(path):
    if not isinstance(path, (str, bytes)):
        return None
    try:
        return Path(os.fsdecode(path)).resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return None


def audit(event, args):
    if event != "open" or not CURRENT:
        return
    name = relative(args[0])
    if name and "/__pycache__/" not in name and not name.endswith((".pyc", ".pyo")):
        if name.endswith(".py") and comparison_profile.is_import_read():
            return
        FILES.setdefault(name, set()).add(CURRENT)


def wrap_constructor(original):
    @functools.wraps(original)
    def traced(self, *args, **kwargs):
        if CURRENT:
            key = (type(self), CURRENT)
            if key not in CONSTRUCTED:
                CONSTRUCTED.add(key)
                for base in type(self).__mro__:
                    if base.__module__.startswith("sqlglot"):
                        CLASSES.setdefault(base.__module__ + ":" + base.__qualname__, set()).add(CURRENT)
        return original(self, *args, **kwargs)
    return traced


CONSTRUCTED = set()


def pytest_sessionstart(session):
    if os.environ.get("BLAST_ATTRIBUTES_ONLY") == "1":
        attribute_loader.install(ROOT, lambda: CURRENT)
    elif os.environ.get("BLAST_COVERAGE_ONLY") != "1":
        comparison_profile.install(ROOT, lambda: CURRENT)
    concurrent.futures.ProcessPoolExecutor = TracedExecutor
    COV.start()
    sys.addaudithook(audit)


def pytest_collection_modifyitems(items):
    TESTS.extend(item.nodeid for item in items)
    if any(os.environ.get(name) == "1" for name in ("BLAST_READS_ONLY", "BLAST_ATTRIBUTES_ONLY", "BLAST_TYPES_ONLY")):
        return
    seen = set()
    for name, module in list(sys.modules.items()):
        if not name.startswith("sqlglot") or module is None:
            continue
        for cls in list(vars(module).values()):
            if not isinstance(cls, type) or cls in seen or not cls.__module__.startswith("sqlglot"):
                continue
            seen.add(cls)
            original = cls.__dict__.get("__init__")
            if inspect.isfunction(original):
                cls.__init__ = wrap_constructor(original)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    global CURRENT
    CURRENT = item.nodeid
    COV.switch_context(CURRENT)
    try:
        return (yield)
    finally:
        EXECUTED.add(item.nodeid)
        CURRENT = ""
        COV.switch_context("")


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef, request):
    global CURRENT
    before = CURRENT
    if fixturedef.scope in ("class", "module", "session"):
        scope = request.node.nodeid
        CURRENT = "@setup:" + scope
        CLASS_SETUP.setdefault(CURRENT, [i for i in TESTS if i.startswith(scope + "::") or not scope])
        COV.switch_context(CURRENT)
    try:
        return (yield)
    finally:
        CURRENT = before
        COV.switch_context(CURRENT)


def pytest_runtest_logreport(report):
    if report.failed or report.skipped:
        REPORTS.setdefault(report.nodeid, []).append({"phase": report.when, "outcome": report.outcome, "reason": str(report.longrepr)[:1500]})


def pytest_sessionfinish(session, exitstatus):
    COV.stop()
    COV.save()
    data = COV.get_data()
    lines = {}
    for filename in data.measured_files():
        name = relative(filename)
        if name:
            lines[name] = {str(n): cs for n, cs in data.contexts_by_lineno(filename).items() if any(cs)}
    OUT.write_text(json.dumps({"tests": TESTS, "executed": sorted(EXECUTED), "lines": lines, "files": {p: sorted(cs) for p, cs in FILES.items()}, "classes": {p: sorted(cs) for p, cs in CLASSES.items()}, "setup": CLASS_SETUP, "reports": REPORTS, "attribute_imports_tracked": True, "attribute_profile": attribute_profile.export(), "comparison_profile": comparison_profile.export(), "dictionary_profile": dictionary_profile.export(), "global_functions_tracked": hasattr(COV, "global_functions"), "global_functions": sorted(getattr(COV, "global_functions", []))}, separators=(",", ":")), encoding="utf-8")
