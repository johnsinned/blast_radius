import ast
from pathlib import Path
import tempfile
import unittest
from tools import attribute_loader as loader, attribute_profile as profile


class AttributeReadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "sqlglot").mkdir()
        for mapping in (profile.FIELDS, profile.DECLARATIONS, profile.UNKNOWN, profile.META_CACHE):
            mapping.clear()
        profile.NAMES.clear()
        profile.IMPORT_DEPTH = 0
        self.context = ""
        profile.CURRENT = lambda: self.context

    def tearDown(self):
        self.directory.cleanup()

    def load(self, source):
        path = self.root / "sqlglot/demo.py"
        path.write_text(source)
        profile.index(self.root)
        tree = loader.Instrument("sqlglot.demo").visit(ast.parse(source))
        ast.fix_missing_locations(tree)
        namespace = {"__name__": "sqlglot.demo", loader.VALUE: profile.value, loader.DYNAMIC: profile.dynamic, loader.NAMESPACE: profile.namespace, loader.REGISTER: profile.register, loader.NAMED: profile.named}
        exec(compile(tree, str(path), "exec"), namespace)
        return namespace

    def test_inherited_setting_resolves_to_its_actual_declaration(self):
        ns = self.load("class Base:\n FLAG = False\nclass Child(Base):\n pass\nclass Override(Base):\n FLAG = True\n")
        self.context = "A"
        self.assertFalse(profile.value(ns["Child"](), "FLAG"))
        self.context = "B"
        self.assertTrue(profile.value(ns["Override"], "FLAG"))
        data = profile.export()
        self.assertEqual(data["sqlglot/demo.py:2:1"]["reads"], ["A"])
        self.assertEqual(data["sqlglot/demo.py:6:1"]["reads"], ["B"])

    def test_class_namespace_tracks_only_values_actually_consumed(self):
        ns = self.load("class Base:\n FLAG = False\n OTHER = True\n")
        self.context = "A"
        namespace = profile.value(ns["Base"], "__dict__")
        self.assertIn("FLAG", list(namespace))
        self.assertEqual(profile.export()["sqlglot/demo.py:2:1"]["reads"], [])
        self.assertFalse(namespace.get("FLAG"))
        self.assertEqual(profile.export()["sqlglot/demo.py:2:1"]["reads"], ["A"])
        self.assertEqual(profile.export()["sqlglot/demo.py:3:1"]["reads"], [])

    def test_dynamic_reads_and_import_time_reads_are_recorded(self):
        ns = self.load("class Base:\n FLAG = False\nx = Base.FLAG\ndef read(obj):\n return getattr(obj, 'FLAG')\n")
        self.context = "B"
        self.assertFalse(ns["read"](ns["Base"]()))
        data = profile.export()["sqlglot/demo.py:2:1"]
        self.assertTrue(data["global"])
        self.assertEqual(data["reads"], ["B"])

    def test_unknown_class_and_external_metaclass_are_conservative(self):
        ns = self.load("class Base:\n FLAG = False\n")
        class External:
            FLAG = True
        self.context = "B"
        profile.value(External, "FLAG")
        self.assertEqual(profile.export()["sqlglot/demo.py:2:1"]["reads"], ["B"])
        class Meta(type):
            def __new__(mcls, name, bases, namespace):
                return super().__new__(mcls, name, bases, namespace)
        self.assertFalse(profile._meta_policy(Meta)[0])

    def test_unqualified_class_namespace_consumption_is_global(self):
        self.load("class Base:\n FLAG = False\n DERIVED = FLAG\n")
        self.assertTrue(profile.export()["sqlglot/demo.py:2:1"]["global"])

    def test_lazy_import_reads_are_global_even_during_a_test(self):
        ns = self.load("class Base:\n FLAG = False\n")
        self.context = "A"
        profile.IMPORT_DEPTH = 1
        try:
            profile.value(ns["Base"], "FLAG")
        finally:
            profile.IMPORT_DEPTH = 0
        self.assertTrue(profile.export()["sqlglot/demo.py:2:1"]["global"])

    def test_class_decorators_disable_setting_pruning(self):
        self.load("def decorate(cls):\n return cls\n@decorate\nclass Base:\n FLAG = False\n")
        self.assertFalse(profile.export()["sqlglot/demo.py:5:1"]["eligible"])


if __name__ == "__main__":
    unittest.main()
