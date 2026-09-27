"""Shared guards separating fresh-clone-safe tests from tests bound to this host.

A host-bound test runs only where its tools, folders or storage exist. Setting
SHAFFY_SKIP_HOST_TESTS=1 skips all of them, which matches CI as long as CI installs
the tools the remaining tests need, including the declared Ansible controller
and collections. Controller tests use synthetic settings and temporary files;
they do not require this machine's configuration.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKIP_HOST = os.environ.get('SHAFFY_SKIP_HOST_TESTS') == '1'


def artifacts_dir():
    """The untracked scratch folder some tests use; created on demand."""
    path = ROOT / 'artifacts'
    path.mkdir(exist_ok=True)
    return path


def _reflink_supported(directory):
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        source = Path(temporary) / 'source'
        source.write_bytes(b'reflink probe')
        result = subprocess.run(['cp', '--reflink=always', str(source), str(source) + '.copy'],
                                capture_output=True, check=False)
        return result.returncode == 0


def host_test(reason, *, tools=(), paths=(), reflink=False):
    """Skip unless every tool and path exists (and reflink copies work, if asked)."""
    missing = [tool for tool in tools if shutil.which(tool) is None]
    missing += [str(path) for path in paths if not Path(path).exists()]
    if reflink and not missing and not _reflink_supported(artifacts_dir()):
        missing.append('reflink-capable storage under artifacts/')
    if SKIP_HOST:
        return unittest.skip(f'host-bound ({reason}); SHAFFY_SKIP_HOST_TESTS=1')
    return unittest.skipIf(bool(missing), f'host-bound ({reason}); missing: {", ".join(missing)}')
