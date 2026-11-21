import ast
import unittest
from tools import comparison_profile as comparisons, method_profile as methods


class OriginalEqualityTests(unittest.TestCase):
    def make(self, body=None, slots=True):
        source = "class Expr:\n"
        if slots:
            source += " __slots__ = ()\n"
        source += " def __eq__(self, other):\n  " + (body or "return self is other or (type(self) is type(other) and hash(self) == hash(other))") + "\n def __ne__(self, other):\n  return not self.__eq__(other)\n"
        tree = ast.parse(source)
        code = compile(tree, "original.py", "exec")
        namespace = {"__name__": "sqlglot.example"}
        methods.register(tree, code, "sqlglot/example.py", namespace)
        exec(code, namespace)
        return namespace["Expr"], namespace

    def test_short_circuit_proof_keeps_hashing_unexecuted(self):
        cls, _ = self.make()
        proof, _ = methods.prove(cls, cls.__eq__, cls.__ne__)
        self.assertTrue(proof["none_eq"])
        self.assertTrue(proof["none_ne"])
        self.assertEqual(proof["proof_files"], ["sqlglot/example.py"])

    def test_unknown_effects_and_truthiness_reject_the_proof(self):
        for body in ("return hash(self) == hash(other)", "return self or False", "return print('effect') or False", "return self is not other"):
            cls, _ = self.make(body)
            self.assertIsNone(methods.prove(cls, cls.__eq__, cls.__ne__))

    def test_instance_method_override_prevents_ne_proof(self):
        cls, _ = self.make(slots=False)
        proof, _ = methods.prove(cls, cls.__eq__, cls.__ne__)
        self.assertTrue(proof["none_eq"])
        self.assertFalse(proof["none_ne"])

    def test_replaced_code_or_type_global_revokes_cached_evidence(self):
        for change_code in (False, True):
            cls, namespace = self.make()
            obj = cls()
            self.assertEqual(comparisons.TYPES[comparisons.describe(obj)]["kind"], "custom_none")
            if change_code:
                cls.__eq__.__code__ = (lambda self, other: True).__code__
            else:
                namespace["type"] = lambda value: int
            self.assertEqual(comparisons.TYPES[comparisons.describe(obj)]["kind"], "unsupported")


if __name__ == "__main__":
    unittest.main()
