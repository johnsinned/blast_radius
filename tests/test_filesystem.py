import hashlib
import unittest

from tools.apply_filesystem_map import trace_access
from test_selector import DependencyFixture, patch_for


class NativeAccessTests(unittest.TestCase):
    def test_native_children_descriptor_paths_and_metadata_are_preserved(self):
        inventory = {"README.md": "x", "sqlglot/demo.py": "x", "data.sql": "x", "other/data.sql": "x"}
        lines = [
            '412 openat(AT_FDCWD, "/opt/sqlglot/data.sql", O_RDONLY) = 3</opt/sqlglot/data.sql>\n',
            '412 newfstatat(3</opt/sqlglot/data.sql>, "", {}, AT_EMPTY_PATH) = 0\n',
            '587 newfstatat(AT_FDCWD, "/opt/sqlglot/README.md", {}, 0) = 0\n',
            '587 openat(AT_FDCWD, "/opt/sqlglot/sqlglot/__pycache__/demo.cpython-313.pyc", O_RDONLY) = 4\n',
        ]
        accessed, metadata, opens = trace_access(lines, inventory)
        self.assertEqual(accessed, {"README.md", "sqlglot/demo.py", "data.sql"})
        self.assertEqual(metadata, {"README.md"})
        self.assertEqual(opens["data.sql"], 1)

    def test_relative_paths_without_cwd_are_matched_conservatively(self):
        inventory = {"a/file.sql": "x", "b/file.sql": "x"}
        accessed, _, _ = trace_access(['77 openat(AT_FDCWD, "file.sql", O_RDONLY) = 3\n'], inventory)
        self.assertEqual(accessed, set(inventory))


class FileViewSelectionTests(DependencyFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.filename = "tests/fixtures/demo.sql"
        self.fixture = "-- ignored label\nSELECT 1;\nSELECT 1;\n"
        file = self.root / self.filename
        file.parent.mkdir(parents=True)
        file.write_text(self.fixture)
        self.map["files"][self.filename] = {"reads": 3}
        self.map["fixture_views"] = {self.filename: "sql_comment_lines"}
        self.map["file_inventory"] = {self.filename: {"sha256": hashlib.sha256(file.read_bytes()).hexdigest(), "accessed": True}}

    def select_fixture(self, after, extra=""):
        return self.selector().select(patch_for(self.fixture, after, self.filename) + extra)[0]

    def test_ignored_comment_and_empty_lines_leave_loader_input_identical(self):
        self.assertEqual(self.select_fixture(self.fixture.replace("ignored label", "new label")), [])
        self.assertEqual(self.select_fixture("\n" + self.fixture + "\n"), [])

    def test_metadata_inline_comments_and_sql_changes_keep_readers(self):
        for after in (self.fixture.replace("-- ignored label", "# dialect: mysql"), self.fixture.replace("SELECT 1;", "SELECT 2;", 1), self.fixture.replace("SELECT 1;", "SELECT 1; -- note", 1)):
            self.assertEqual(self.select_fixture(after), self.tests)

    def test_missing_proof_digest_mismatch_and_multiple_files_keep_fallback(self):
        after = self.fixture.replace("ignored label", "new label")
        other = patch_for(self.source, "# note\n" + self.source)
        self.assertEqual(self.select_fixture(after, other), self.tests)
        self.map["file_inventory"][self.filename]["sha256"] = "mismatch"
        self.assertEqual(self.select_fixture(after), self.tests)

    def test_unused_existing_files_skip_but_known_access_and_deletion_do_not(self):
        name = "unused.md"
        file = self.root / name
        file.write_text("old\n")
        record = {"accessed": False, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
        self.map["file_inventory"][name] = record
        patch = patch_for("old\n", "new\n", name)
        self.assertEqual(self.selector().select(patch)[0], [])
        record["accessed"] = True
        self.assertEqual(self.selector().select(patch)[0], self.tests)
        record["mode_only"] = True
        self.assertEqual(self.selector().select(patch)[0], [])
        mode = "diff --git a/unused.md b/unused.md\nold mode 100644\nnew mode 100755\n"
        self.assertEqual(self.selector().select(mode)[0], self.tests)
        record["accessed"] = False
        removal = "diff --git a/unused.md b/unused.md\n--- a/unused.md\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"
        self.assertEqual(self.selector().select(removal)[0], self.tests)


if __name__ == "__main__": unittest.main()
