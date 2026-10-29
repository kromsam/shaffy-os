"""A snapper pre/post pair around one sensitive command run by the user.

The home apply takes its own pair; the explicit commands that move or resume
personal data (the Zotero profile move, a Nextcloud folder resume) use this, so
each can be compared with or restored from the state just before it. They refuse to run without the snapper `home`
configuration, which the system apply creates.
"""

from contextlib import contextmanager
import sys

from .apply import ApplyError, adapter
from .selection import state_dir


class SnapshotError(Exception):
    pass


@contextmanager
def home_pair(description):
    """Yield the pre snapshot's number; the post snapshot follows even when the command fails."""
    snapper = adapter(state_dir())
    problems = snapper.problems()
    if problems:
        raise SnapshotError('no snapshot can be taken first, so nothing was changed:\n  ' + '\n  '.join(problems))
    try:
        pre = snapper.snapshot('pre', description)
    except ApplyError as error:
        raise SnapshotError(f'{error}; nothing was changed') from error
    print(f'Snapper pre snapshot {pre} (config home) holds the home from before: {description}.', file=sys.stderr)
    try:
        yield pre
    finally:
        post = snapper.snapshot('post', description, pre=pre)
        print(
            f'Snapper post snapshot {post} pairs with {pre}; compare them with snapper -c home status {pre}..{post}.',
            file=sys.stderr,
        )
