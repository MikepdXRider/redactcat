"""Auth service utilities.

Shared helpers used by both the auth router (key generation/storage) and the
auth dependency (key lookup). Kept here to avoid a circular import between
routers/auth.py and dependencies.py.
"""

import hashlib

API_KEY_PREFIX = "rcat_"
KEY_PREFIX_DISPLAY_CHARS = 8


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()
