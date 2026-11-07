"""Required native execution cannot green through omitted or broken test collection."""

import tempfile
import unittest
from pathlib import Path

from scripts.check_python_contracts import junit_cases, require_native_inventory


class NativeEnrollmentTests(unittest.TestCase):
    def report(self, text):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "junit.xml"
        path.write_text(f"<testsuites><testsuite>{text}</testsuite></testsuites>")
        return path

    def test_exact_passing_execution_is_accepted(self):
        path = self.report('<testcase classname="tests.source_contracts.matrix" name="revision"/>')
        require_native_inventory(path, ["tests.source_contracts.matrix::revision"])

    def test_omitted_case_fails_even_when_pytest_was_successful(self):
        path = self.report('<testcase classname="tests.source_contracts.matrix" name="token"/>')
        with self.assertRaisesRegex(RuntimeError, "native enrollment differs"):
            require_native_inventory(
                path, ["tests.source_contracts.matrix::token", "tests.source_contracts.matrix::revision"]
            )

    def test_zero_skipped_duplicate_and_import_errors_are_not_detection(self):
        for body in (
            "",
            '<testcase classname="tests.source_contracts.matrix" name="revision"><skipped/></testcase>',
            '<testcase classname="tests.source_contracts.matrix" name="revision"><error>ImportError</error></testcase>',
            '<testcase classname="tests.source_contracts.matrix" name="revision"/>' * 2,
        ):
            with self.subTest(body=body), self.assertRaises(RuntimeError):
                junit_cases(self.report(body))


if __name__ == "__main__":
    unittest.main()
