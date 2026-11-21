import ast
import json
from pathlib import Path
import pickle
import sys
import unittest
import tempfile
import types

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from semantic_values import snapshot, restore, compare, dictionary_key_id
from selector import semantic_changes
from selector import Selector, PIN
from tools import dictionary_profile
from tools import comparison_profile


class PrimitiveFixtureTests(unittest.TestCase):
    def test_exact_primitive_round_trip(self):
        for value in [None, True, -31, -0.0, 1.25, b"\xff\x00", "snow", (1, "a"), [True, None], {"x": [1, 2]}]:
            with self.subTest(value=value):
                result = restore(json.loads(json.dumps(snapshot(value))))
                self.assertEqual(type(result), type(value))
                self.assertEqual(result, value)

    def test_snapshot_does_not_convert_user_objects(self):
        class Dangerous:
            def __iter__(self):
                raise AssertionError("must not iterate a user object")
        with self.assertRaises(ValueError):
            snapshot(Dangerous())
        with self.assertRaises(ValueError):
            snapshot(float("nan"))
        with self.assertRaises(ValueError):
            snapshot("x" * 129)

    def test_identity_uses_original_alias_information(self):
        self.assertFalse(compare("Is", "same", "same", alias=False))
        self.assertTrue(compare("Eq", "same", "same", alias=False))

    def test_predicate_changes_and_literal_edits(self):
        before = ast.parse("def f(x):\n return x < (23, 12)\n")
        self.assertTrue(semantic_changes(before, ast.parse("def f(x):\n return x <= (23, 12)\n")))
        self.assertTrue(semantic_changes(before, ast.parse("def f(x):\n return x < (24, 1)\n")))
        self.assertIsNone(semantic_changes(before, ast.parse("def f(x):\n return g(x) < (24, 1)\n")))

    def test_dictionary_edits_require_unchanged_keys_and_literal_values(self):
        before = ast.parse("TABLE = {'a': 1, 'b': (2, 3)}\n")
        self.assertTrue(semantic_changes(before, ast.parse("TABLE = {'a': 2, 'b': (2, 3)}\n")))
        self.assertIsNone(semantic_changes(before, ast.parse("TABLE = {'a': 2, 'c': (2, 3)}\n")))
        self.assertIsNone(semantic_changes(before, ast.parse("TABLE = {'a': call(), 'b': (2, 3)}\n")))


class DictionaryReadTests(unittest.TestCase):
    def setUp(self):
        dictionary_profile.SITES.clear()
        dictionary_profile.CURRENT = lambda: "test"
        self.value = dictionary_profile.observe("site", {"a": 1, "b": 2})

    def test_key_reads_are_distinct_from_shape(self):
        self.assertEqual(self.value.get("a"), 1)
        self.assertIn("b", self.value)
        self.assertEqual(len(self.value), 2)
        profile = dictionary_profile.export()["site"]
        self.assertEqual(profile["keys"], {dictionary_key_id("a"): ["test"]})
        self.assertEqual(profile["whole"], [])

    def test_copy_and_unpack_consult_values(self):
        self.assertEqual(dict(self.value), {"a": 1, "b": 2})
        profile = dictionary_profile.export()["site"]
        self.assertEqual(set(profile["keys"]), {dictionary_key_id("a"), dictionary_key_id("b")})
        dictionary_profile.SITES.clear()
        result = {**self.value}
        self.assertEqual(result["b"], 2)
        self.assertTrue(dictionary_profile.export()["site"]["keys"])

    def test_serialization_reads_values(self):
        self.assertEqual(json.loads(json.dumps(self.value)), {"a": 1, "b": 2})
        self.assertEqual(dictionary_profile.export()["site"]["whole"], ["test"])
        self.assertEqual(pickle.loads(pickle.dumps(self.value)), {"a": 1, "b": 2})

    def test_global_reads_are_not_test_local(self):
        dictionary_profile.CURRENT = lambda: ""
        self.value["a"]
        profile = dictionary_profile.export()["site"]
        self.assertIn(dictionary_key_id("a"), profile["global_keys"])

    def test_unbound_copy_and_update_consult_values(self):
        self.assertEqual(dict.copy(self.value), {"a": 1, "b": 2})
        profile = dictionary_profile.export()["site"]
        self.assertTrue(profile["keys"] or profile["whole"])
        dictionary_profile.SITES.clear()
        target = {}
        target.update(self.value)
        self.assertEqual(target["a"], 1)
        self.assertTrue(dictionary_profile.export()["site"]["keys"])

    def test_inequality_reads_both_dictionaries(self):
        other = dictionary_profile.observe("other", {"a": 5})
        self.assertNotEqual(self.value, other)
        profile = dictionary_profile.export()
        self.assertEqual(profile["site"]["whole"], ["test"])
        self.assertEqual(profile["other"]["whole"], ["test"])


class ImportReadTests(unittest.TestCase):
    def test_importer_reads_are_separate_from_source_inspection(self):
        events = []
        class Original:
            def get_source(self, fullname):
                events.append(comparison_profile.is_import_read())
                return "VALUE = 1\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            loader = comparison_profile.Loader(Original(), str(root / "demo.py"), root)
            loader.exec_module(types.ModuleType("sqlglot.demo"))
            loader.get_source("sqlglot.demo")
        self.assertEqual(events, [True, False])


class DictionarySelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "sqlglot").mkdir()
        self.source = "TABLE = {'a': 1, 'b': 2}\n"
        (self.root / "sqlglot/demo.py").write_text(self.source)
        self.tests = [{"id": "A", "number": 5, "seconds": 1}, {"id": "B", "number": 9, "seconds": 1}]
        self.map = {"pin": PIN, "tests": ["A", "B"], "sets": ["0", "1", "2", "3"], "comparison_profiled_tests": 3, "comparison_types": {}, "comparisons": {}, "files": {"sqlglot/demo.py": {"lines": {}, "reads": 0, "coverage": 3, "scopes": [], "statements": [[1, 1, 1]], "assignments": [{"start": 1, "end": 1, "users": 3}]}}, "dictionaries": {"sqlglot/demo.py:1:8": {"global_whole": False, "global_keys": [], "whole": 0, "keys": {dictionary_key_id("a"): 1, dictionary_key_id("b"): 2}}}}

    def tearDown(self):
        self.temporary.cleanup()

    def select(self):
        import difflib
        self.map_path = self.root / "map.json"
        self.map_path.write_text(json.dumps(self.map))
        after = self.source.replace("'b': 2", "'b': 3")
        patch = "diff --git a/sqlglot/demo.py b/sqlglot/demo.py\n" + "".join(difflib.unified_diff(self.source.splitlines(True), after.splitlines(True), fromfile="a/sqlglot/demo.py", tofile="b/sqlglot/demo.py"))
        return [t["number"] for t in Selector(self.tests, self.root, self.map_path).select(patch)[0]]

    def test_only_readers_of_changed_values_are_selected(self):
        self.assertEqual(self.select(), [9])

    def test_import_time_value_reads_prevent_pruning(self):
        self.map["dictionaries"]["sqlglot/demo.py:1:8"]["global_keys"] = [dictionary_key_id("b")]
        self.assertEqual(self.select(), [5, 9])

    def test_whole_dictionary_reads_remain_selected(self):
        self.map["dictionaries"]["sqlglot/demo.py:1:8"]["whole"] = 1
        self.assertEqual(self.select(), [5, 9])


if __name__ == "__main__":
    unittest.main()
