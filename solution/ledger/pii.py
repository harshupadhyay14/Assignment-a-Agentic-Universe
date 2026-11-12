"""PII protection at rest.

* Phone numbers are matched through a keyed hash (HMAC-SHA256): deterministic, so events can
  be joined to leads, but not reversible without the key (and not rainbow-tableable, unlike a
  bare SHA-256 over a 10^9 phone space).
* Values we may need to read back later (name, phone) are encrypted with Fernet (AES-128-CBC
  + HMAC) under a key derived from the same master secret.

Master secret: $LEDGER_PII_KEY, otherwise a random key generated once and kept in
<db>.key (chmod 600). In production this comes from a KMS / secret manager, not a file
beside the database.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet


class PiiVault:
    def __init__(self, master: bytes):
        self._hash_key = hmac.new(master, b"ledger/phone-hash/v1", hashlib.sha256).digest()
        enc = hmac.new(master, b"ledger/field-encryption/v1", hashlib.sha256).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(enc))

    @classmethod
    def for_database(cls, db_path: str) -> "PiiVault":
        env = os.environ.get("LEDGER_PII_KEY")
        if env:
            return cls(env.encode())
        key_path = Path(str(db_path) + ".key")
        if key_path.exists():
            return cls(key_path.read_bytes().strip())
        key = secrets.token_hex(32).encode()
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return cls(key)

    def phone_hash(self, canonical_phone: str) -> str:
        return hmac.new(self._hash_key, canonical_phone.encode(), hashlib.sha256).hexdigest()

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()
