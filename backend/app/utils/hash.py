"""
Hash utility — generate SHA256 hash from file bytes.

Used to create tamper-proof document fingerprints before
storing on the Polygon blockchain.
"""

import hashlib


def generate_hash(file_bytes: bytes) -> str:
    """Generate a SHA256 hex digest from raw file bytes.

    Parameters
    ----------
    file_bytes : bytes
        Raw bytes of the uploaded document.

    Returns
    -------
    str
        64-character SHA256 hex digest string.
    """
    sha256 = hashlib.sha256()
    sha256.update(file_bytes)

    return sha256.hexdigest()
