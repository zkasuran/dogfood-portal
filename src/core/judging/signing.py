"""Ed25519 signing for results bundles and judge records.

The DEPLOYMENT generates this keypair on first boot and keeps the private key in
its own volume. The public key travels inside every bundle and is served at a
well-known endpoint, so anyone can verify a result without trusting the host and
without any key of ours. This is never the operator's wallet key, the product is
meant to be forked and self-hosted so it ships no personal key.
"""
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

_RAW = serialization.Encoding.Raw
_RAWPUB = serialization.PublicFormat.Raw


def generate_private_pem():
    """A fresh Ed25519 private key as PEM bytes, for the deployment to store."""
    key = Ed25519PrivateKey.generate()
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def load_private_pem(pem_bytes):
    return serialization.load_pem_private_key(pem_bytes, password=None)


def public_hex(private_key):
    """Raw 32 byte public key as hex, the form that goes in the bundle."""
    return private_key.public_key().public_bytes(_RAW, _RAWPUB).hex()


def sign_hex(private_key, message):
    """Sign message bytes, return the 64 byte signature as hex."""
    return private_key.sign(message).hex()


def verify_hex(public_key_hex, message, signature_hex):
    """True if signature_hex is a valid signature of message under the given public key."""
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pub.verify(bytes.fromhex(signature_hex), message)
        return True
    except Exception:
        return False
