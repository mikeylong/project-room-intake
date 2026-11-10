"""Intentional temporary failure for branch-protection verification; never merge."""
import sys
import unittest

FAIL_VERSION = (3, 14)


class RequiredChecksProbe(unittest.TestCase):
    def test_disposable_required_check(self):
        if sys.version_info[:2] == FAIL_VERSION:
            self.fail("Intentional disposable PR probe: this required CI check must block merging")


if __name__ == "__main__":
    unittest.main()
