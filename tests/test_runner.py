"""The parallel runner behind `./bin/shaffy selftest`, on a synthetic suite."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runner  # noqa: E402
import support  # noqa: E402

SUITE = '''
import unittest

calls = []


class Passing(unittest.TestCase):
    def test_one(self):
        print('output of a passing test')

    def test_two(self):
        pass

    @unittest.skip('not here')
    def test_skipped(self):
        pass


class Failing(unittest.TestCase):
    def test_fails(self):
        print('output of a failing test')
        self.assertEqual(1, 2)

    def test_errs(self):
        raise RuntimeError('broken')

    def test_subtests(self):
        for number in (1, 2):
            with self.subTest(number=number):
                self.assertEqual(number, 1)


class Shared(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        calls.append('setUpClass')

    def test_first(self):
        self.assertEqual(calls, ['setUpClass'])

    def test_second(self):
        self.assertEqual(calls, ['setUpClass'])


class BrokenFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raise RuntimeError('fixture')

    def test_never_runs(self):
        pass
'''


class RunnerTests(unittest.TestCase):
    def run_suite(self, source, *arguments):
        with tempfile.TemporaryDirectory() as directory:
            shutil.copy(HERE / 'runner.py', directory)
            (Path(directory) / 'test_synthetic.py').write_text(textwrap.dedent(source))
            return subprocess.run([sys.executable, str(Path(directory) / 'runner.py'), '--jobs', '2', *arguments],
                                  capture_output=True, text=True, timeout=120, check=False)

    def test_failures_and_errors_fail_the_run_with_their_output(self):
        result = self.run_suite(SUITE, '-v')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('FAIL: test_synthetic.Failing.test_fails', result.stdout)
        self.assertIn('output of a failing test', result.stdout)
        self.assertNotIn('output of a passing test', result.stdout)
        self.assertIn('ERROR: test_synthetic.Failing.test_errs', result.stdout)
        self.assertIn('RuntimeError: broken', result.stdout)
        self.assertIn('FAIL: test_synthetic.Failing.test_subtests (number=2)', result.stdout)
        self.assertNotIn('(number=1)', result.stdout)
        self.assertIn('ERROR: setUpClass (test_synthetic.BrokenFixture)', result.stdout)
        self.assertIn('test_synthetic.Passing.test_skipped ... skipped: not here', result.stdout)
        self.assertIn('Ran 8 tests', result.stdout)
        self.assertIn('FAILED (failures=2, errors=2, skipped=1)', result.stdout)

    def test_a_class_with_its_own_fixture_stays_whole_and_a_serial_test_waits(self):
        class Plain(unittest.TestCase):
            def test_a(self):
                pass

            def test_b(self):
                pass

        class Shared(unittest.TestCase):
            @classmethod
            def tearDownClass(cls):
                pass

            def test_a(self):
                pass

            def test_b(self):
                pass

        class Timed(unittest.TestCase):
            @support.serial
            def test_timing(self):
                pass

        tests = [Plain('test_a'), Plain('test_b'), Shared('test_a'), Shared('test_b'), Timed('test_timing')]
        parallel, serial = runner.units(tests)
        self.assertEqual(parallel, [[tests[0].id()], [tests[1].id()], [tests[2].id(), tests[3].id()]])
        self.assertEqual(serial, [[tests[4].id()]])

    def test_a_passing_suite_succeeds(self):
        result = self.run_suite('import unittest\n\n\nclass Passing(unittest.TestCase):\n'
                                '    def test_one(self):\n        pass\n')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Ran 1 tests', result.stdout)
        self.assertTrue(result.stdout.rstrip().endswith('OK'), result.stdout)


if __name__ == '__main__':
    unittest.main()
