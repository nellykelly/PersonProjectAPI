#!/usr/bin/env python
"""Generate the RSA key pair for the MARKET_SVC Snowflake service user.

    python tools/snowflake_keygen.py                  # -> S:\\.secrets\\snowflake\\
    python tools/snowflake_keygen.py --out-dir D:\\keys

Writes rsa_key.p8 (private, PKCS#8 PEM, unencrypted) and rsa_key.pub
(public PEM) OUTSIDE the repo, refuses to overwrite an existing key, then
prints the single-line public key to paste into snowflake/setup.sql and
the SHA-256 fingerprint to compare against `DESC USER MARKET_SVC`.

The private key never goes in the repo, .env, chat, or Snowflake -- only
its path goes in .env (SNOWFLAKE_PRIVATE_KEY_PATH). Rotate by generating
a second key and setting RSA_PUBLIC_KEY_2 on the user, then removing the
first; Snowflake allows two active keys for zero-downtime rotation.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

DEFAULT_DIR = Path(r"S:\.secrets\snowflake")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args()

    out = args.out_dir
    private_path = out / "rsa_key.p8"
    public_path = out / "rsa_key.pub"
    repo = Path(__file__).resolve().parents[2]
    if out.resolve().is_relative_to(repo):
        print(f"Refusing to write keys inside the repo ({repo}). Pick a directory outside it.")
        return 1
    if private_path.exists():
        print(f"{private_path} already exists -- not overwriting a live key. Delete it first to rotate.")
        return 1

    out.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_path.write_bytes(public_pem)

    der = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    body = "".join(line for line in public_pem.decode().splitlines() if "-----" not in line)
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(der).digest()).decode()

    print(f"Private key: {private_path}   (keep it here; put only this PATH in .env)")
    print(f"Public key:  {public_path}")
    print()
    print("Paste this into snowflake/setup.sql where it says <PASTE_PUBLIC_KEY_HERE>:")
    print(body)
    print()
    print(f"After setup, `DESC USER MARKET_SVC` should show RSA_PUBLIC_KEY_FP = {fingerprint}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
