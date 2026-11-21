from pathlib import Path
import sys
import tempfile
import unittest
from tools.factory_coverage import FactoryCoverage


@unittest.skipUnless(hasattr(sys, "monitoring"), "requires CPython monitoring")
class FactoryInvocationTests(unittest.TestCase):
    def collect(self, source, global_call=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sqlglot").mkdir()
            file = root / "sqlglot/demo.py"
            file.write_text(source)
            context = ""
            coverage = FactoryCoverage(root, lambda: context)
            coverage.start()
            try:
                namespace = {}
                exec(compile(source, str(file), "exec"), namespace)
                if global_call: namespace["a"]()
                context = "A"
                self.assertTrue(namespace["a"]())
                context = "B"
                self.assertFalse(namespace["b"]())
            finally:
                coverage.stop()
            result = coverage.export()
            self.assertTrue(result["valid"])
            return result["sites"]

    def test_distinct_callbacks_keep_their_actual_users(self):
        source = "def factory(value):\n def callback():\n  return value\n return callback\na = factory(True)\nb = factory(False)\n"
        data = self.collect(source)
        self.assertEqual(data["sqlglot/demo.py:5:4"]["users"], ["A"])
        self.assertEqual(data["sqlglot/demo.py:6:4"]["users"], ["B"])
        self.assertTrue(data["sqlglot/demo.py:5:4"]["eligible"])

    def test_import_time_invocations_disable_pruning(self):
        source = "def factory(value):\n def callback():\n  return value\n return callback\na = factory(True)\nb = factory(False)\n"
        self.assertTrue(self.collect(source, global_call=True)["sqlglot/demo.py:5:4"]["global"])

    def test_side_effects_and_input_dependent_headers_are_rejected(self):
        for middle in (" marker.append(value)\n", ""):
            source = "marker = []\ndef factory(value):\n" + middle + " def callback(default=value):\n  return default\n return callback\na = factory(True)\nb = factory(False)\n"
            self.assertEqual(self.collect(source), {})

    def test_creation_without_an_observed_return_is_not_eligible(self):
        source = "def factory(value):\n def callback():\n  return value\n return callback\ntry:\n skipped = factory(True, False)\nexcept TypeError:\n pass\na = factory(True)\nb = factory(False)\n"
        data = self.collect(source)
        self.assertTrue(any(not record["eligible"] for record in data.values()))


if __name__ == "__main__": unittest.main()
