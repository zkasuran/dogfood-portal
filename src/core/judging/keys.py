"""Load or create the deployment's Ed25519 signing key.

The key is generated once on first boot and kept in the deployment's own volume. It
is never a personal or shared key, because the product is meant to be forked and
self-hosted. This module never logs, prints or returns the private key bytes. Callers
get the loaded key object or the public hex only.
"""
import os

import signing  # top-level; resolved by core.judging.__init__ path setup


def get_or_create_private_key(path):
    """Return (private_key, generated). Load the PEM at `path`, or generate one and
    write it owner-only on first call. Idempotent. Never prints the key."""
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return signing.load_private_pem(fh.read()), False

    pem = signing.generate_private_pem()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    # Create owner read/write only, then write, so the key is never briefly world
    # readable on a shared volume.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    os.chmod(path, 0o600)
    return signing.load_private_pem(pem), True


def public_key_hex(path):
    """The raw Ed25519 public key as hex, creating the key on first call if needed."""
    key, _ = get_or_create_private_key(path)
    return signing.public_hex(key)
