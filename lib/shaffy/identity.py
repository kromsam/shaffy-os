"""Git signing is rendered only when its key exists."""
from pathlib import Path


def signing_note(settings, home):
    """A note when signing is requested but the key is missing, so commits would go unsigned."""
    key = settings.get('identity', {}).get('signing', {}).get('key')
    if not key:
        return None
    path = Path(home) / key[2:] if key.startswith('~/') else Path(key)
    if path.is_file():
        return None
    return (f'identity.signing.key: {path} does not exist, so Git commits stay unsigned until it '
            'does; apply again once the key is restored')
