"""The parallel test runner behind `./bin/shaffy selftest`.

The tests stay plain unittest tests. Each process discovers the same suite, and a pool of
workers takes it in units: a class with its own setUpClass or tearDownClass, or any class of a
module with setUpModule, runs whole in one worker, and every other test is a unit of its own,
so the long end-to-end classes spread over all workers. A test marked with `support.serial`,
such as a latency measurement that a busy machine would skew, runs afterwards on its own.
A passing test's output is discarded; a failing one's is shown with its traceback.
"""
import argparse
from collections import Counter
import concurrent.futures
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SYMBOLS = {'ok': '.', 'FAIL': 'F', 'ERROR': 'E', 'skipped': 's', 'expected failure': 'x',
           'unexpected success': 'u'}
# This process's tests by id: the parent's discovery, or a worker's own.
TESTS = {}


def _flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _flatten(test)
        else:
            yield test


def _load():
    if not TESTS:
        suite = unittest.defaultTestLoader.discover(HERE, pattern='test_*.py', top_level_dir=HERE)
        TESTS.update((test.id(), test) for test in _flatten(suite))
    return TESTS


def _shares_fixtures(test):
    cls = type(test)
    overridden = any(getattr(cls, name).__func__ is not getattr(unittest.TestCase, name).__func__
                     for name in ('setUpClass', 'tearDownClass'))
    return overridden or hasattr(sys.modules.get(cls.__module__), 'setUpModule')


def _serial(test):
    method = getattr(test, test._testMethodName, None)
    return getattr(test, 'shaffy_serial', False) or getattr(method, 'shaffy_serial', False)


def units(tests):
    """The parallel and the serial units, each a list of test ids, in discovery order."""
    parallel, serial, classes = [], [], {}
    for test in tests:
        target = serial if _serial(test) else parallel
        if not _shares_fixtures(test):
            target.append([test.id()])
            continue
        key = (target is serial, type(test))
        if key not in classes:
            classes[key] = []
            target.append(classes[key])
        classes[key].append(test.id())
    return parallel, serial


class _Result(unittest.TestResult):
    """Each outcome as (test id, status, detail, seconds)."""

    def __init__(self):
        super().__init__()
        self.buffer = True
        self.outcomes = []
        self._started = time.monotonic()

    def startTest(self, test):
        self._started = time.monotonic()
        super().startTest(test)

    def _add(self, test, status, detail=''):
        self.outcomes.append((test.id(), status, detail, time.monotonic() - self._started))

    def addSuccess(self, test):
        super().addSuccess(test)
        self._add(test, 'ok')

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._add(test, 'FAIL', self.failures[-1][1])

    def addError(self, test, err):
        super().addError(test, err)
        self._add(test, 'ERROR', self.errors[-1][1])

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            failed = issubclass(err[0], test.failureException)
            self._add(subtest, 'FAIL' if failed else 'ERROR', (self.failures if failed else self.errors)[-1][1])

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._add(test, 'skipped', reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._add(test, 'expected failure')

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._add(test, 'unexpected success')


def run_unit(ids):
    """Runs one unit in this process; returns the number of tests run and their outcomes."""
    tests = _load()
    result = _Result()
    unittest.TestSuite(tests[test_id] for test_id in ids).run(result)
    return result.testsRun, result.outcomes


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run the test suite in parallel worker processes.')
    parser.add_argument('-j', '--jobs', type=int, default=os.process_cpu_count() or 1, metavar='N')
    parser.add_argument('-v', '--verbose', action='store_true', help='list every test and the slowest')
    args = parser.parse_args(argv)
    parallel, serial = units(_load().values())
    started = time.monotonic()
    count, outcomes = 0, []

    def report(ran, batch):
        nonlocal count
        count += ran
        for test_id, status, detail, seconds in batch:
            outcomes.append((test_id, status, detail, seconds))
            if args.verbose:
                reason = f': {detail}' if status == 'skipped' else ''
                print(f'{test_id} ... {status}{reason} ({seconds:.1f}s)', flush=True)
            else:
                print(SYMBOLS[status], end='', flush=True)

    with concurrent.futures.ProcessPoolExecutor(max(args.jobs, 1), initializer=_load) as pool:
        futures = {pool.submit(run_unit, unit): unit for unit in parallel}
        for future in concurrent.futures.as_completed(futures):
            try:
                report(*future.result())
            except Exception as error:  # a worker that died takes its unit with it
                report(len(futures[future]), [(test_id, 'ERROR', f'the worker failed: {error!r}', 0.0)
                                              for test_id in futures[future]])
    for unit in serial:
        report(*run_unit(unit))
    elapsed = time.monotonic() - started

    if not args.verbose:
        print()
    for test_id, status, detail, _ in outcomes:
        if status in ('FAIL', 'ERROR'):
            print('=' * 70, f'{status}: {test_id}', '-' * 70, detail, sep='\n')
    if args.verbose:
        print('-' * 70, 'Slowest:', sep='\n')
        for test_id, _, _, seconds in sorted(outcomes, key=lambda outcome: -outcome[3])[:10]:
            print(f'{seconds:7.1f}s  {test_id}')
    print('-' * 70)
    print(f'Ran {count} tests in {elapsed:.3f}s with {args.jobs} workers\n')
    statuses = Counter(status for _, status, _, _ in outcomes)
    labels = (('FAIL', 'failures'), ('ERROR', 'errors'), ('skipped', 'skipped'),
              ('expected failure', 'expected failures'), ('unexpected success', 'unexpected successes'))
    details = ', '.join(f'{label}={statuses[status]}' for status, label in labels if statuses[status])
    failed = statuses['FAIL'] or statuses['ERROR'] or statuses['unexpected success']
    print(('FAILED' if failed else 'OK') + (f' ({details})' if details else ''))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
