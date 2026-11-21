"""Record pristine Python file opens and verified SQL fixture input views."""
import ast
import builtins
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
from pathlib import PosixPath, WindowsPath
import sys
import types
from baseline_executor import TracedExecutor
from bytecode_model import fingerprint, walk_codes

ROOT = Path(os.environ["BLAST_SOURCE"]).resolve()
OUT = Path(os.environ["BLAST_RAW_FILES"])
OPEN = builtins.open
FILES, REPORTS, TESTS, EXECUTED = {}, {}, [], set()
SAFE_STATS = {}
STAT, LSTAT = os.stat, os.lstat
MODE_CHECKS = {function.__code__ for function in (os.path.isfile, os.path.isdir, os.path.islink, os.path.exists, os.path.lexists, Path.is_file, Path.is_dir, Path.is_symlink, Path.exists) if hasattr(function, "__code__")}
PATH_STAT = {Path.stat.__code__, Path.lstat.__code__}
HELPERS = ROOT / "tests/helpers.py"
SOURCE = HELPERS.read_text("utf-8")
tree = ast.parse(SOURCE)
expected = ast.parse('def _filter_comments(s):\n return "\\n".join([line for line in s.splitlines() if line and not line.startswith("--")])').body[0]
actual = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_filter_comments")
if ast.dump(actual) != ast.dump(expected):
    raise RuntimeError("fixture filter does not match the supported input view")
CODES = {code.co_name: fingerprint(code) for code in walk_codes(compile(HELPERS.read_bytes(), str(HELPERS), "exec", dont_inherit=True)) if code.co_name in ("_filter_comments", "load_sql_fixtures", "load_sql_fixture_pairs")}
CACHE = {}


def filtered_reader():
    frame = sys._getframe(1)
    for _ in range(18):
        if frame is None: break
        code = frame.f_code
        if code.co_filename == str(HELPERS) and code.co_name in ("load_sql_fixtures", "load_sql_fixture_pairs"):
            if code not in CACHE: CACHE[code] = fingerprint(code) == CODES[code.co_name]
            filter_fn = frame.f_globals.get("_filter_comments")
            opener = frame.f_globals.get("open", frame.f_builtins.get("open"))
            return CACHE[code] and opener is OPEN and builtins.open is OPEN and type(filter_fn) is types.FunctionType and fingerprint(filter_fn.__code__) == CODES["_filter_comments"]
        frame = frame.f_back
    return False


def audit(event, args):
    if event != "open" or not isinstance(args[0], (str, bytes, os.PathLike)): return
    filename = os.path.abspath(os.fsdecode(args[0]))
    try: relative = Path(filename).relative_to(ROOT).as_posix()
    except ValueError: return
    record = FILES.setdefault(relative, {"opens": 0, "raw": False})
    record["opens"] += 1
    if args[1] not in ("r", "rt") or not filtered_reader(): record["raw"] = True


def stat_wrapper(original):
    def tracked(path, *args, **kwargs):
        frame = sys._getframe(1)
        while frame and frame.f_code in PATH_STAT: frame = frame.f_back
        safe = frame is not None and frame.f_code in MODE_CHECKS
        supported = type(path) in (str, bytes, PosixPath, WindowsPath)
        if safe and supported and kwargs.get("dir_fd") is None and type(original) is types.BuiltinFunctionType:
            filename = os.path.abspath(os.fsdecode(path))
            try: relative = Path(filename).relative_to(ROOT).as_posix()
            except ValueError: relative = None
            if relative is not None: SAFE_STATS[relative] = SAFE_STATS.get(relative, 0) + 1
        return original(path, *args, **kwargs)
    return tracked


def pytest_sessionstart(session):
    concurrent.futures.ProcessPoolExecutor = TracedExecutor
    os.stat, os.lstat = stat_wrapper(STAT), stat_wrapper(LSTAT)
    sys.addaudithook(audit)


def pytest_collection_modifyitems(items): TESTS.extend(item.nodeid for item in items)
def pytest_runtest_logreport(report):
    if report.when == "teardown": EXECUTED.add(report.nodeid)
    if report.failed or report.skipped: REPORTS.setdefault(report.nodeid, []).append(report.outcome)


def pytest_sessionfinish(session, exitstatus):
    OUT.write_text(json.dumps({"tests": TESTS, "executed": sorted(EXECUTED), "reports": REPORTS, "exit": exitstatus, "opens": FILES, "safe_stats": SAFE_STATS, "helper_sha256": hashlib.sha256(SOURCE.encode()).hexdigest()}, separators=(",", ":")), encoding="utf-8")
