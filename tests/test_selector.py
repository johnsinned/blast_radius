import difflib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from selector import PIN, Selector, apply_change, parse_patch, schedule, split
from score import score_run
from tools.baseline_executor import TracedExecutor
from tools import comparison_profile
from selector import identity_only_changes
import ast
import enum


class Serializable:
    dumps = 0
    loads = 0

    def __getstate__(self):
        type(self).dumps += 1
        return {}

    def __setstate__(self, state):
        type(self).loads += 1


def identity(value):
    return value


class BaselineExecutorTests(unittest.TestCase):
    def test_inputs_and_outputs_use_serialization(self):
        Serializable.dumps = Serializable.loads = 0
        original = Serializable()
        with TracedExecutor() as executor:
            result = executor.submit(identity, original).result()
        self.assertIsNot(result, original)
        self.assertEqual(Serializable.dumps, 2)
        self.assertEqual(Serializable.loads, 2)


class IdentityTypeTests(unittest.TestCase):
    def kind(self, value):
        return comparison_profile.TYPES[comparison_profile.describe(value)]["kind"]

    def test_identity_types_and_overloads(self):
        class Plain:
            pass
        class Overloaded:
            def __eq__(self, other):
                return True
        class NeOnly:
            def __ne__(self, other):
                return False
        self.assertEqual(self.kind(None), "identity")
        self.assertEqual(self.kind(Plain()), "identity")
        self.assertEqual(self.kind(Overloaded()), "unsupported")
        self.assertEqual(self.kind(NeOnly()), "unsupported")
        self.assertEqual(self.kind(42), "unsupported")

    def test_mutating_equality_revokes_the_summary(self):
        class Plain:
            pass
        obj = Plain()
        self.assertEqual(self.kind(obj), "identity")
        Plain.__eq__ = lambda self, other: True
        self.assertEqual(self.kind(obj), "unsupported")

    def test_metaclass_equality_and_hash_are_not_invoked(self):
        class Meta(type):
            def __hash__(cls):
                raise AssertionError("profiling must not hash a user class")
            def __eq__(cls, other):
                raise AssertionError("profiling must not compare a user class")
        class Plain(metaclass=Meta):
            pass
        self.assertEqual(self.kind(Plain()), "identity")

    def test_str_enum_requires_canonical_members(self):
        class Mode(str, enum.Enum):
            A = "a"
            B = "b"
        self.assertEqual(self.kind(Mode.A), "canonical_str_enum")
        fake = str.__new__(Mode, "a")
        self.assertEqual(self.kind(fake), "unsupported")

    def test_changing_operands_or_polarity_is_not_equivalence(self):
        before = ast.parse("def f(x):\n return x is None\n")
        self.assertTrue(identity_only_changes(before, ast.parse("def f(x):\n return x == None\n")))
        self.assertIsNone(identity_only_changes(before, ast.parse("def f(x):\n return x != None\n")))
        self.assertIsNone(identity_only_changes(before, ast.parse("def f(x):\n return x == 1\n")))


def patch_for(before, after, filename="sqlglot/demo.py"):
    return f"diff --git a/{filename} b/{filename}\n" + "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="a/" + filename, tofile="b/" + filename))


class PatchTests(unittest.TestCase):
    def test_multiple_hunks_and_files(self):
        before = "".join(f"x{i} = {i}\n" for i in range(30))
        after = before.replace("x1 = 1", "x1 = 42").replace("x28 = 28", "x28 = 0")
        changes = parse_patch(patch_for(before, after) + patch_for("a = 1\n", "a = 2\n", "sqlglot/other.py"))
        self.assertEqual(len(changes), 2)
        self.assertEqual(len(changes[0].hunks), 2)
        self.assertEqual(apply_change(before, changes[0]), after)

    def test_insertion_at_end(self):
        before, after = "x = 1\n", "x = 1\ny = 2\n"
        change, = parse_patch(patch_for(before, after))
        self.assertEqual(apply_change(before, change), after)
        self.assertEqual(change.anchors, {2})

    def test_mismatched_source(self):
        change, = parse_patch(patch_for("x = 1\n", "x = 2\n"))
        with self.assertRaises(ValueError):
            apply_change("x = 99\n", change)

    def test_no_final_newline(self):
        text = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x=1\n\\ No newline at end of file\n+x=2\n\\ No newline at end of file\n"
        change, = parse_patch(text)
        self.assertEqual(apply_change("x=1", change), "x=2")


class WorkerTests(unittest.TestCase):
    def test_empty_and_short_suites(self):
        self.assertEqual(schedule([]), [])
        self.assertEqual(schedule([{"number": 8, "seconds": 0.01}]), [[8]])

    def test_worker_choice_includes_startup_cost(self):
        tests = [{"number": i, "seconds": 1} for i in range(4)]
        self.assertEqual(len(schedule(tests)), 1)
        tests = [{"number": i, "seconds": 20} for i in range(8)]
        self.assertEqual(len(schedule(tests)), 8)

    def test_output_valid_and_best_among_all_worker_counts(self):
        tests = [{"number": i, "seconds": ((i * 37) % 91) / 10} for i in range(73)]
        workers = schedule(tests)
        report = score_run((json.dumps({"commit": "x", "workers": workers}) + "\n").encode(), tests, {"x": {"failing": [t["number"] for t in tests]}})
        self.assertTrue(report["ok"])
        self.assertEqual(report["misses"], 0)
        for count in range(1, 9):
            other = split(tests, count)
            score = score_run((json.dumps({"commit": "x", "workers": other}) + "\n").encode(), tests, {"x": {"failing": []}})
            self.assertLessEqual(report["simulated_milliseconds"], score["simulated_milliseconds"])


class DependencyFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "sqlglot").mkdir()
        self.source = "def f():\n    return 1\n\ndef g():\n    return 2\n"
        (self.root / "sqlglot/demo.py").write_text(self.source)
        self.tests = [{"id": "B", "number": 17, "seconds": .1}, {"id": "A", "number": 99, "seconds": .2}]
        self.map = {"pin": PIN, "tests": ["A", "B"], "sets": ["0", "1", "2", "3"], "files": {"sqlglot/demo.py": {"lines": {"2": 1, "5": 2}, "reads": 0, "coverage": 3, "scopes": [{"start": 1, "end": 2, "kind": "function", "coverage": 1}, {"start": 4, "end": 5, "kind": "function", "coverage": 2}], "statements": [[1, 2, 1], [2, 2, 2], [4, 5, 4], [5, 5, 5]], "assignments": []}}}

    def tearDown(self):
        self.tmp.cleanup()

    def selector(self):
        path = self.root / "map.json"
        path.write_text(json.dumps(self.map))
        return Selector(self.tests, self.root, path)


class DependencyTests(DependencyFixture, unittest.TestCase):
    def test_ids_are_remapped_to_scorer_numbers(self):
        selected, _ = self.selector().select(patch_for(self.source, self.source.replace("return 1", "return 42")))
        self.assertEqual([t["number"] for t in selected], [99])

    def test_comments_keep_source_read_dependencies(self):
        self.map["files"]["sqlglot/demo.py"]["reads"] = 2
        selected, _ = self.selector().select(patch_for(self.source, "# hi\n" + self.source))
        self.assertEqual([t["number"] for t in selected], [17])

    def test_syntax_error_selects_everything(self):
        selected, _ = self.selector().select(patch_for(self.source, self.source.replace("return 1", "return (")))
        self.assertEqual(selected, self.tests)

    def test_encoding_cookie_is_interpreted(self):
        before = self.source.replace("return 1", "return r'\\u0041'")
        after = "# coding: unicode_escape\n" + before
        (self.root / "sqlglot/demo.py").write_text(before)
        selected, _ = self.selector().select(patch_for(before, after))
        # This changes string semantics despite editing only a comment line.
        self.assertEqual(selected, self.tests)

    def test_absent_map_selects_everything(self):
        selected, _ = Selector(self.tests, self.root, self.root / "absent.json").select("bad patch")
        self.assertEqual(selected, self.tests)

    def test_unmapped_file_selects_everything(self):
        selected, _ = self.selector().select(patch_for("x=1\n", "x=2\n", "other.py"))
        self.assertEqual(selected, self.tests)

    def test_unknown_test_kept_even_for_comments(self):
        self.tests.append({"id": "NEW", "number": 100, "seconds": 0})
        selected, _ = self.selector().select(patch_for(self.source, "# hi\n" + self.source))
        self.assertEqual([t["number"] for t in selected], [100])

    def test_unsafe_path_refused(self):
        with self.assertRaises(ValueError):
            self.selector().read_source("../secret")


class EquivalenceSelectionTests(DependencyFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.source = "def f(x):\n    return x is None\n\ndef g():\n    return 2\n"
        (self.root / "sqlglot/demo.py").write_text(self.source)
        self.map["comparison_profiled_tests"] = 3
        self.map["comparison_types"] = {"plain": {"kind": "identity"}, "none": {"kind": "identity"}, "overloaded": {"kind": "unsupported"}}
        self.map["comparisons"] = {"sqlglot/demo.py:2:11": {"global": False, "pairs": [["plain", "none", 1]]}}

    def edit(self, after=None):
        return patch_for(self.source, after or self.source.replace(" is None", " == None"))

    def test_safe_comparison_can_select_nothing(self):
        selected, _ = self.selector().select(self.edit())
        self.assertEqual(selected, [])

    def test_overloaded_equality_keeps_the_test(self):
        self.map["comparisons"]["sqlglot/demo.py:2:11"]["pairs"].append(["overloaded", "none", 1])
        selected, _ = self.selector().select(self.edit())
        self.assertEqual([t["number"] for t in selected], [99])

    def test_different_string_enum_classes_are_not_equivalent(self):
        self.map["comparison_types"].update({"enum1": {"kind": "canonical_str_enum"}, "enum2": {"kind": "canonical_str_enum"}})
        self.map["comparisons"]["sqlglot/demo.py:2:11"]["pairs"] = [["enum1", "enum2", 1]]
        selected, _ = self.selector().select(self.edit())
        self.assertEqual([t["number"] for t in selected], [99])

    def test_unprofiled_test_kept(self):
        self.map["comparison_profiled_tests"] = 2
        selected, _ = self.selector().select(self.edit())
        self.assertEqual([t["number"] for t in selected], [99])

    def test_global_operation_disables_equivalence(self):
        self.map["comparisons"]["sqlglot/demo.py:2:11"]["global"] = True
        selected, _ = self.selector().select(self.edit())
        self.assertEqual([t["number"] for t in selected], [99])

    def test_source_readers_kept(self):
        self.map["files"]["sqlglot/demo.py"]["reads"] = 1
        selected, _ = self.selector().select(self.edit())
        self.assertEqual([t["number"] for t in selected], [99])

    def test_extra_semantic_change_disables_equivalence(self):
        selected, _ = self.selector().select(self.edit(self.source.replace(" is None", " == None").replace("return 2", "return 3")))
        self.assertEqual({t["number"] for t in selected}, {17, 99})

    def test_multiple_files_disable_equivalence(self):
        selected, _ = self.selector().select(self.edit() + patch_for(self.source, "# comment\n" + self.source))
        self.assertEqual([t["number"] for t in selected], [99])


if __name__ == "__main__":
    unittest.main()
