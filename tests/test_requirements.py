from collections import defaultdict
import sys
import unittest

from test_selector import DependencyFixture, patch_for
from tools.requirement_coverage import RequirementCoverage
from tools.dictionary_profile import ObservedDict
from bytecode_model import fingerprint


class Expression:
    arg_types = ObservedDict("site", {"this": True, "optional": False})
    required_args = {"this"}

    def __init__(self, args):
        self.args = args

    def error_messages(self, profile):
        profile.record(sys._getframe().f_code, 0)


class RequirementInputTests(unittest.TestCase):
    def profile(self):
        result = RequirementCoverage.__new__(RequirementCoverage)
        result.current = lambda: "A"
        result.sites = {"site": ["this", "optional"]}
        result.cache = {}
        result.initializer = None
        result.validator = fingerprint(Expression.error_messages.__code__)
        result.unsafe = defaultdict(lambda: defaultdict(set))
        result.global_keys = defaultdict(set)
        result.raw = defaultdict(set)
        result.global_raw = set()
        result.unknown = set()
        result.failures = defaultdict(int)
        return result

    def test_only_none_and_empty_lists_are_missing(self):
        for value in (0, False, "", (), [], None, [1]):
            with self.subTest(value=value):
                profile = self.profile()
                Expression({"this": "present", "optional": value}).error_messages(profile)
                self.assertEqual(profile.unsafe["site"]["optional"], {"A"} if value is None or type(value) is list and not value else set())
                self.assertFalse(profile.raw)

    def test_inherited_dictionary_identity_is_retained(self):
        class Child(Expression): pass
        profile = self.profile()
        Child({"this": "present"}).error_messages(profile)
        self.assertEqual(profile.unsafe["site"]["optional"], {"A"})

    def test_list_subclasses_and_modified_metadata_keep_consumers(self):
        class CustomList(list): pass
        for args in ({"this": CustomList([1])}, {"this": "valid"}):
            profile = self.profile()
            node = Expression(args)
            if type(args["this"]) is str: node.required_args = set()
            node.error_messages(profile)
            self.assertEqual(profile.raw["site"], {"A"})

    def test_multiple_existing_errors_keep_even_present_keys(self):
        class Child(Expression):
            arg_types = ObservedDict("site", {"this": True, "second": True, "optional": False})
            required_args = {"this", "second"}
        profile = self.profile()
        Child({"optional": "present"}).error_messages(profile)
        self.assertEqual(profile.unsafe["site"]["optional"], {"A"})

    def test_import_time_missing_inputs_are_global_dependencies(self):
        profile = self.profile()
        profile.current = lambda: ""
        Expression({"this": "present"}).error_messages(profile)
        self.assertEqual(profile.global_keys["site"], {"optional"})


class RequirementSelectionTests(DependencyFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.source = 'class Example:\n    arg_types = {"this": True, "optional": False}\n'
        (self.root / "sqlglot/demo.py").write_text(self.source)
        self.map["files"]["sqlglot/demo.py"]["scopes"] = [{"start": 1, "end": 2, "kind": "class", "coverage": 3}]
        self.map["files"]["sqlglot/demo.py"]["assignments"] = [{"start": 2, "end": 2, "users": 3}]
        self.map["requirement_profiled_tests"] = 3
        self.profile = {"global": False, "global_keys": [], "raw": 0, "keys": {"this": 1, "optional": 2}}
        self.map["requirements"] = {"proof": {"proof_files": ["sqlglot/core.py"], "proof_spans": {"sqlglot/core.py": [[1, 10]]}}, "unknown": 0, "sites": {"sqlglot/demo.py:2:16": self.profile}}

    def select_edit(self, after=None, extra=""):
        return self.selector().select(patch_for(self.source, after or self.source.replace('"optional": False', '"optional": True')) + extra)[0]

    def test_only_inputs_missing_the_new_required_argument_remain(self):
        self.assertEqual([t["id"] for t in self.select_edit()], ["B"])
        self.assertEqual([t["id"] for t in self.select_edit(self.source.replace('"this": True', '"this": False'))], ["A"])

    def test_raw_consumers_unknown_and_incomplete_tests_remain(self):
        for key in ("raw",):
            self.profile[key] = 1
            self.assertEqual(self.select_edit(), self.tests)
            self.profile[key] = 0
        self.map["requirements"]["unknown"] = 1
        self.assertEqual(self.select_edit(), self.tests)
        self.map["requirements"]["unknown"] = 0
        self.map["requirement_profiled_tests"] = 2
        self.assertEqual(self.select_edit(), self.tests)

    def test_source_inspection_cannot_be_removed(self):
        self.map["files"]["sqlglot/demo.py"]["reads"] = 1
        self.assertEqual(self.select_edit(), self.tests)

    def test_import_time_copies_and_missing_inputs_disable_refinement(self):
        self.profile["global"] = True
        self.assertEqual(self.select_edit(), self.tests)
        self.profile["global"] = False
        self.profile["global_keys"] = ["optional"]
        self.assertEqual(self.select_edit(), self.tests)

    def test_key_nonboolean_and_unrelated_changes_use_ordinary_dependencies(self):
        for after in (self.source.replace('"optional"', '"renamed"'), self.source.replace('"optional": False', '"optional": 1'), self.source.replace('"optional": False', '"optional": True') + 'OTHER = 1\n'):
            self.assertEqual(self.select_edit(after), self.tests)
        extra = patch_for("x=1\n", "x=2\n", "other.py")
        self.assertEqual(self.select_edit(extra=extra), self.tests)

    def test_proof_changes_disable_refinement(self):
        self.map["requirements"]["proof"] = {"proof_files": ["sqlglot/demo.py"], "proof_spans": {"sqlglot/demo.py": [[1, 10]]}}
        self.assertEqual(self.select_edit(), self.tests)


if __name__ == "__main__": unittest.main()
