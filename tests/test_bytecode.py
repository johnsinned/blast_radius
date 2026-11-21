import ast
import difflib
import json
import itertools
from pathlib import Path
import sys
import tempfile
import unittest

from bytecode_model import aligned_changes, changed_offsets, fingerprint, walk_codes
from selector import PIN, Selector
from tools.block_coverage import BlockCoverage


class AlignmentWitnessTests(unittest.TestCase):
    def test_changed_outcomes_have_an_executed_divergence_witness(self):
        expressions = ("a", "b", "c", "a and b", "a or b", "(a and b) or c", "a and (b or c)", "(a if b else c)", "max(a, b)", "a + b", "bool(a and b)", "a == b", "not b", "None", "(b or None)", "b < c")
        codes = []
        inputs = list(itertools.product((False, True), repeat=3)) + list(itertools.product((-1, 0, 2), repeat=3))
        for expression in expressions:
            namespace = {}
            source = f"def f(a, b, c):\n if a:\n  return {expression}\n return max(b, c)\n"
            exec(compile(source, "alignment-fixture", "exec"), namespace)
            fn = namespace["f"]
            runs = []
            for args in inputs:
                executed = {0}
                def trace(frame, event, argument):
                    if frame.f_code is fn.__code__:
                        frame.f_trace_opcodes = True
                        executed.add(frame.f_lasti)
                        return trace
                    return None
                try:
                    sys.settrace(trace)
                    value = fn(*args)
                    outcome = (type(value), value)
                except Exception as error:
                    outcome = (type(error), str(error))
                finally:
                    sys.settrace(None)
                runs.append((outcome, executed))
            codes.append((fn.__code__, runs))
        witnesses = 0
        for old, old_runs in codes:
            for new, new_runs in codes:
                changed = aligned_changes(old, new)
                if changed is None: continue
                for (before, reached), (after, _) in zip(old_runs, new_runs):
                    if before != after:
                        self.assertTrue(reached.intersection(changed), (old.co_consts, new.co_consts, before, after, changed, reached))
                        witnesses += 1
        self.assertGreater(witnesses, 1000)

    def test_exception_regions_generators_and_layout_changes_keep_fallback(self):
        for source, after in (("def f(x):\n try:\n  return x\n except ValueError:\n  return None\n", "def f(x):\n try:\n  return not x\n except ValueError:\n  return None\n"), ("def f(x):\n yield x\n", "def f(x):\n yield not x\n"), ("def f(x):\n return x\n", "def f(x):\n y=x\n return y\n")):
            old = compile(source, "demo", "exec").co_consts[0]
            new = compile(after, "demo", "exec").co_consts[0]
            self.assertIsNone(aligned_changes(old, new))


@unittest.skipUnless(hasattr(sys, "monitoring"), "requires CPython monitoring")
class InstructionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "sqlglot").mkdir()
        self.source = "def f(a, b, c):\n    if a and (b and c):\n        return 1\n    return 0\n"
        self.context = ""

    def tearDown(self):
        self.directory.cleanup()

    def make_selector(self, call_globally=False):
        path = self.root / "sqlglot/demo.py"
        path.write_text(self.source)
        coverage = BlockCoverage(self.root, lambda: self.context)
        coverage.start()
        try:
            namespace = {}
            exec(compile(self.source, str(path), "exec"), namespace)
            if call_globally:
                namespace["f"](True, True, False)
            self.context = "A"
            coverage.switch_context(self.context)
            namespace["f"](False, True, True)
            self.context = "B"
            coverage.switch_context(self.context)
            namespace["f"](True, True, False)
        finally:
            coverage.stop()
        raw = coverage.export()
        self.assertTrue(raw["valid"])
        code = next(code for code in walk_codes(compile(ast.parse(self.source), str(path), "exec", dont_inherit=True)) if code.co_name == "f")
        record = raw["codes"]["sqlglot/demo.py:1:f"]
        self.assertEqual(record["fingerprint"], fingerprint(code))
        numbers = {"A": 1, "B": 2}
        record["blocks"] = {offset: [sum(numbers[name] for name in block["contexts"]), block["global"]] for offset, block in record["blocks"].items()}
        profile = {"python": list(sys.version_info[:2]), "completed": 3, "codes": {"sqlglot/demo.py:1:f": record}}
        tests = [{"id": "A", "number": 1, "seconds": .1}, {"id": "B", "number": 2, "seconds": .1}]
        mapping = {"pin": PIN, "tests": ["A", "B"], "sets": ["0", "1", "2", "3"], "bytecode_profile": profile, "files": {"sqlglot/demo.py": {"lines": {"2": 3, "3": 3, "4": 3}, "reads": 0, "coverage": 3, "scopes": [{"name": "f", "start": 1, "end": 4, "kind": "function", "coverage": 3}], "statements": [[2, 3, 2], [3, 3, 3], [4, 4, 4]], "assignments": []}}}
        file = self.root / "map.json"
        file.write_text(json.dumps(mapping))
        return Selector(tests, self.root, file)

    def select(self, selector, replacement):
        patch = "diff --git a/sqlglot/demo.py b/sqlglot/demo.py\n" + "".join(difflib.unified_diff(self.source.splitlines(True), replacement.splitlines(True), fromfile="a/sqlglot/demo.py", tofile="b/sqlglot/demo.py"))
        return [test["id"] for test in selector.select(patch)[0]]

    def test_unreached_nested_condition_does_not_select_outer_short_circuit(self):
        selector = self.make_selector()
        self.assertEqual(self.select(selector, self.source.replace("b and c", "b or c")), ["B"])

    def test_precedence_changes_retain_tests_reaching_earlier_changed_jumps(self):
        self.source = self.source.replace("(b and c)", "b and c")
        selector = self.make_selector()
        self.assertEqual(self.select(selector, self.source.replace("b and c", "b or c")), ["A", "B"])

    def test_global_execution_and_changed_instruction_layout_keep_fallback(self):
        selector = self.make_selector(call_globally=True)
        self.assertEqual(self.select(selector, self.source.replace("b and c", "b or c")), ["A", "B"])
        self.assertEqual(self.select(selector, self.source.replace("return 1", "return max(a, b, c)")), ["A", "B"])

    def test_source_readers_and_unprofiled_tests_are_retained(self):
        selector = self.make_selector()
        selector.map["files"]["sqlglot/demo.py"]["reads"] = 1
        self.assertEqual(self.select(selector, self.source.replace("b and c", "b or c")), ["A", "B"])
        selector.map["files"]["sqlglot/demo.py"]["reads"] = 0
        selector.map["bytecode_profile"]["completed"] = 2
        self.assertEqual(self.select(selector, self.source.replace("b and c", "b or c")), ["A", "B"])

    def test_interpreter_and_code_identity_must_match(self):
        selector = self.make_selector()
        selector.map["bytecode_profile"]["codes"]["sqlglot/demo.py:1:f"]["fingerprint"] = "mismatched code"
        self.assertEqual(self.select(selector, self.source.replace("b and c", "b or c")), ["A", "B"])
        selector = self.make_selector()
        selector.map["bytecode_profile"]["python"] = [0, 0]
        self.assertEqual(self.select(selector, self.source.replace("b and c", "b or c")), ["A", "B"])

    def test_inserted_condition_in_nested_return_keeps_only_reaching_tests(self):
        self.source = "def f(a, b, c):\n    if a:\n        return max(b, c)\n    return 0\n"
        selector = self.make_selector()
        after = self.source.replace("max(b, c)", "max(b or c, c)")
        old = list(walk_codes(compile(self.source, "demo", "exec")))[1]
        new = list(walk_codes(compile(after, "demo", "exec")))[1]
        self.assertIsNone(changed_offsets(old, new))
        self.assertIsNotNone(aligned_changes(old, new))
        self.assertEqual(self.select(selector, after), ["B"])

    def test_inserted_entry_work_retains_every_caller(self):
        self.source = "def f(a, b, c):\n    if a:\n        return max(b, c)\n    return 0\n"
        selector = self.make_selector()
        after = self.source.replace("    if a:", "    max(b, c)\n    if a:")
        self.assertEqual(self.select(selector, after), ["A", "B"])


if __name__ == "__main__":
    unittest.main()
