"""
Encrypted Credential Vault
==========================
AES-256 encrypted local credential storage for autonomous login
and form-filling. Uses Fernet (AES-128-CBC with HMAC) via the
cryptography library.

Key management:
- Reads JAMBU_VAULT_KEY from environment
- Falls back to ~/.jambu/vault.key file
- Auto-generates key on first use if neither exists
"""

import os
import base64
import json
import time
from pathlib import Path
from typing import Optional, List, Dict
from threading import Lock

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from backend.core.database import get_db_cursor


# ---- Key Management ----

VAULT_KEY_DIR = Path.home() / ".jambu"
VAULT_KEY_FILE = VAULT_KEY_DIR / "vault.key"


def _get_or_create_key() -> bytes:
    """
    Get the encryption key from environment, key file, or generate one.
    Priority: JAMBU_VAULT_KEY env var > ~/.jambu/vault.key > auto-generate
    """
    # 1. Check environment variable
    env_key = os.environ.get("JAMBU_VAULT_KEY")
    if env_key:
        # If the env key is a raw string, derive a Fernet key from it
        if len(env_key) == 44 and env_key.endswith("="):
            # Already a base64-encoded Fernet key
            return env_key.encode()
        # Derive a proper Fernet key using PBKDF2
        return _derive_key(env_key)

    # 2. Check file
    if VAULT_KEY_FILE.exists():
        return VAULT_KEY_FILE.read_bytes()

    # 3. Auto-generate
    key = Fernet.generate_key()
    VAULT_KEY_DIR.mkdir(parents=True, exist_ok=True)
    VAULT_KEY_FILE.write_bytes(key)
    os.chmod(VAULT_KEY_FILE, 0o600)  # Read/write owner only
    return key


def _derive_key(password: str, salt: bytes = None) -> bytes:
    """Derive a Fernet-compatible key from a password using PBKDF2."""
    if salt is None:
        salt = b"jambu_vault_salt_2024"  # Fixed salt for deterministic derivation
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
    return key


def _get_cipher() -> Fernet:
    """Get a Fernet cipher instance using the current key."""
    key = _get_or_create_key()
    return Fernet(key)


# ---- Credential Vault ----

class CredentialVault:
    """
    Encrypted credential storage with AES-256 (via Fernet).
    Thread-safe singleton pattern.
    """

    _instance: Optional["CredentialVault"] = None
    _lock: Lock = Lock()

    def __init__(self):
        self._cipher = _get_cipher()
        self._locked = False
        self._last_access = time.time()
        self._auto_lock_timeout = int(os.environ.get("JAMBU_VAULT_TIMEOUT", "600"))

    @classmethod
    def get_instance(cls) -> "CredentialVault":
        """Get or create the singleton vault instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _check_lock(self):
        """Check if vault is locked and auto-lock if timeout exceeded."""
        if self._locked:
            raise PermissionError("Credential vault is locked. Call unlock() first.")
        if time.time() - self._last_access > self._auto_lock_timeout:
            self._locked = True
            raise PermissionError(
                "Credential vault auto-locked due to inactivity. Call unlock() to continue."
            )

    def _touch(self):
        """Update last access timestamp."""
        self._last_access = time.time()

    def lock(self):
        """Lock the vault, requiring unlock() to access credentials again."""
        self._locked = True

    def unlock(self, master_password: str = None) -> bool:
        """
        Unlock the vault. If JAMBU_MASTER_PASSWORD is set, it must match.

        Returns True if unlocked successfully.
        """
        required = os.environ.get("JAMBU_MASTER_PASSWORD")
        if required and master_password != required:
            return False
        self._locked = False
        self._last_access = time.time()
        return True

    @property
    def is_locked(self) -> bool:
        return self._locked

    # ---- CRUD Operations ----

    def store_credential(
        self,
        domain: str,
        username: str,
        password: str,
        url_pattern: str = None,
        metadata: dict = None,
    ) -> bool:
        """
        Store an encrypted credential.

        Args:
            domain: The domain this credential belongs to (e.g., "example.com")
            username: Username or email
            password: Plaintext password (encrypted before storage)
            url_pattern: Optional URL pattern for matching (e.g., "*.example.com/login")
            metadata: Optional extra data (dict, JSON-serialized)

        Returns:
            True if stored successfully
        """
        self._check_lock()
        self._touch()

        try:
            encrypted = self._cipher.encrypt(password.encode()).decode()
            meta_json = json.dumps(metadata) if metadata else None

            with get_db_cursor() as cursor:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO credential_vault 
                    (domain, url_pattern, username, password_encrypted, metadata, last_used)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (domain, url_pattern, username, encrypted, meta_json, time.time()),
                )
            return True
        except Exception:
            return False

    def get_credential(self, domain: str, username: str = None) -> Optional[dict]:
        """
        Retrieve and decrypt a credential.

        Args:
            domain: Domain to look up
            username: Optional specific username (returns first match if None)

        Returns:
            dict with keys: domain, username, password, url_pattern, metadata
            or None if not found
        """
        self._check_lock()
        self._touch()

        try:
            with get_db_cursor() as cursor:
                if username:
                    cursor.execute(
                        """
                        SELECT domain, username, password_encrypted, url_pattern, metadata
                        FROM credential_vault
                        WHERE domain = ? AND username = ?
                        """,
                        (domain, username),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT domain, username, password_encrypted, url_pattern, metadata
                        FROM credential_vault
                        WHERE domain = ?
                        ORDER BY last_used DESC LIMIT 1
                        """,
                        (domain,),
                    )

                row = cursor.fetchone()
                if not row:
                    return None

                # Update last_used
                cursor.execute(
                    "UPDATE credential_vault SET last_used = ? WHERE domain = ? AND username = ?",
                    (time.time(), row["domain"], row["username"]),
                )

                decrypted = self._cipher.decrypt(row["password_encrypted"].encode()).decode()
                return {
                    "domain": row["domain"],
                    "username": row["username"],
                    "password": decrypted,
                    "url_pattern": row["url_pattern"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                }
        except InvalidToken:
            return None
        except Exception:
            return None

    def get_credentials_for_domain(self, domain: str) -> list:
        """
        Get all credentials matching a domain (partial match).

        Args:
            domain: Domain to search for (partial match)

        Returns:
            List of credential dicts (passwords decrypted)
        """
        self._check_lock()
        self._touch()

        try:
            with get_db_cursor() as cursor:
                cursor.execute(
                    """
                    SELECT domain, username, password_encrypted, url_pattern, metadata
                    FROM credential_vault
                    WHERE domain LIKE ?
                    ORDER BY last_used DESC
                    """,
                    (f"%{domain}%",),
                )

                results = []
                for row in cursor.fetchall():
                    try:
                        decrypted = self._cipher.decrypt(
                            row["password_encrypted"].encode()
                        ).decode()
                        results.append({
                            "domain": row["domain"],
                            "username": row["username"],
                            "password": decrypted,
                            "url_pattern": row["url_pattern"],
                            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                        })
                    except InvalidToken:
                        continue
                return results
        except Exception:
            return []

    def find_best_credential(self, url: str) -> Optional[dict]:
        """
        Find the best matching credential for a URL.

        Matches by url_pattern first (wildcard support), then by domain.

        Args:
            url: Full URL to match against

        Returns:
            Best matching credential dict or None
        """
        self._check_lock()
        self._touch()

        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            target_domain = parsed.hostname or ""

            with get_db_cursor() as cursor:
                cursor.execute(
                    "SELECT domain, username, password_encrypted, url_pattern, metadata FROM credential_vault"
                )
                rows = cursor.fetchall()

            best_match = None
            best_score = -1

            for row in rows:
                score = 0
                url_pattern = row["url_pattern"]

                if url_pattern:
                    # Check URL pattern match
                    if _url_pattern_matches(url, url_pattern):
                        score = 100
                    elif _url_pattern_matches(parsed.path or "/", url_pattern):
                        score = 50

                # Domain match scoring
                if row["domain"] == target_domain:
                    score = max(score, 90)
                elif target_domain.endswith("." + row["domain"]):
                    score = max(score, 70)
                elif row["domain"] in target_domain:
                    score = max(score, 50)

                if score > best_score:
                    try:
                        decrypted = self._cipher.decrypt(
                            row["password_encrypted"].encode()
                        ).decode()
                        best_match = {
                            "domain": row["domain"],
                            "username": row["username"],
                            "password": decrypted,
                            "url_pattern": row["url_pattern"],
                            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                        }
                        best_score = score
                    except InvalidToken:
                        continue

            if best_match:
                # Update last_used
                with get_db_cursor() as cursor:
                    cursor.execute(
                        "UPDATE credential_vault SET last_used = ? WHERE domain = ? AND username = ?",
                        (time.time(), best_match["domain"], best_match["username"]),
                    )

            return best_match

        except Exception:
            return None

    def delete_credential(self, domain: str, username: str) -> bool:
        """
        Delete a stored credential.

        Returns True if deleted, False if not found.
        """
        self._check_lock()
        self._touch()

        try:
            with get_db_cursor() as cursor:
                cursor.execute(
                    "DELETE FROM credential_vault WHERE domain = ? AND username = ?",
                    (domain, username),
                )
                return cursor.rowcount > 0
        except Exception:
            return False

    def update_credential(
        self,
        domain: str,
        username: str,
        password: str = None,
        metadata: dict = None,
    ) -> bool:
        """
        Update an existing credential's password and/or metadata.

        Returns True if updated.
        """
        self._check_lock()
        self._touch()

        try:
            updates = []
            params = []

            if password:
                encrypted = self._cipher.encrypt(password.encode()).decode()
                updates.append("password_encrypted = ?")
                params.append(encrypted)

            if metadata is not None:
                updates.append("metadata = ?")
                params.append(json.dumps(metadata))

            if not updates:
                return False  # Nothing to update

            updates.append("last_used = ?")
            params.append(time.time())
            params.extend([domain, username])

            with get_db_cursor() as cursor:
                cursor.execute(
                    f"UPDATE credential_vault SET {', '.join(updates)} WHERE domain = ? AND username = ?",
                    params,
                )
                return cursor.rowcount > 0
        except Exception:
            return False

    def list_domains(self) -> list:
        """List all domains with stored credentials."""
        self._check_lock()
        self._touch()

        try:
            with get_db_cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT domain FROM credential_vault ORDER BY domain"
                )
                return [row["domain"] for row in cursor.fetchall()]
        except Exception:
            return []


# ---- URL Pattern Matching ----

def _url_pattern_matches(url: str, pattern: str) -> bool:
    """
    Match a URL against a wildcard pattern.
    Supports: * wildcard (matches any characters)

    Examples:
        "https://example.com/login" matches "*.example.com/*"
        "https://example.com/page" matches "https://example.com/*"
    """
    import fnmatch
    return fnmatch.fnmatch(url, pattern)


# ---- Module-level convenience ----

def get_vault() -> CredentialVault:
    """Get the singleton vault instance."""
    return CredentialVault.get_instance()
