"""Runs the 200-scenario security corpus as part of the test suite."""
import os
import subprocess
import sys
import unittest


class TestSecurityCorpus(unittest.TestCase):
    def test_corpus_acceptance_criteria(self):
        repo = os.path.join(os.path.dirname(__file__), "..", "..")
        runner = os.path.join(repo, "security", "run_corpus.py")
        result = subprocess.run([sys.executable, runner],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         msg=result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
