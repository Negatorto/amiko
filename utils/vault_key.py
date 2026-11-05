"""
AMIKO — Vault Key Protection & OS Keyring Integration
Decouples the Vault Data Encryption Key (DEK) from local disk storage (`data/.amiko_key`)
using Envelope Encryption and multi-backend OS Keyring integration:

Supported Protection Modes (`data/vault_meta.json`):
  1. `local_file`:
     DEK is stored in `data/.amiko_key` (chmod 0600) for unattended startup (default).
  2. `passphrase`:
     DEK is wrapped using a Key Encryption Key (KEK) derived from the Admin Master Passphrase
     via PBKDF2-HMAC-SHA256 (600,000 iterations + 16-byte random salt).
     `data/.amiko_key` is securely overwritten and removed from disk.
     On startup (unless `AMIKO_MASTER_PASSPHRASE` or `AMIKO_VAULT_KEY` is set in env),
     the vault starts in SEALED state and requires unsealing in RAM via Web UI or CLI.
  3. `os_keyring`:
     DEK is stored in the Operating System's native credential store / kernel keyring:
       - Python `keyring` library (if installed with a valid backend)
       - Freedesktop Secret Service (`secret-tool` / GNOME Keyring / KDE Wallet)
       - Linux Kernel User Keyring (`@u` via `keyctl` or direct `libc` syscalls)
       - macOS Keychain (`security` CLI)
     `data/.amiko_key` is securely overwritten and removed from disk.
     Optionally stores a PBKDF2-SHA256 recovery envelope in `vault_meta.json` so that if
     the volatile kernel keyring is cleared after a full host power-cycle, the admin can
     unseal via passphrase and automatically re-populate the OS keyring.
"""

import os
import sys
import json
import base64
import hashlib
import secrets
import shutil
import platform
import subprocess
from datetime import datetime, timezone
from cryptography.fernet import Fernet, InvalidToken


VAULT_MODES = ("local_file", "passphrase", "os_keyring")
KDF_ITERATIONS = 600_000
KEYRING_SERVICE = "amiko-vault"
KEYRING_ACCOUNT = "master-dek"
LINUX_KEY_DESC = "amiko:vault-dek"
VERIFIER_PLAINTEXT = b"AMIKO_VAULT_VERIFIED_V1"


def key_fingerprint(dek: bytes) -> str:
    """Return an 8-character non-sensitive hexadecimal fingerprint of a Fernet DEK."""
    if not dek:
        return "sealed"
    return hashlib.sha256(dek.strip()).hexdigest()[:8]


def derive_kek_fernet(passphrase: str, salt: bytes, iterations: int = KDF_ITERATIONS) -> Fernet:
    """Derive a 256-bit Fernet Key Encryption Key (KEK) from a Master Passphrase."""
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        passphrase.encode("utf-8"),
        salt,
        iterations,
        dklen=32
    )
    return Fernet(base64.urlsafe_b64encode(dk))


def wrap_dek_with_passphrase(dek: bytes, passphrase: str) -> dict:
    """Encrypt the DEK with a passphrase-derived KEK and return envelope metadata."""
    salt = os.urandom(16)
    kek = derive_kek_fernet(passphrase, salt, KDF_ITERATIONS)
    wrapped = kek.encrypt(dek.strip()).decode("ascii")
    return {
        "kdf": "PBKDF2-HMAC-SHA256",
        "iterations": KDF_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "wrapped_key": wrapped,
    }


def unwrap_dek_with_passphrase(envelope: dict, passphrase: str) -> bytes:
    """Decrypt the wrapped DEK using the provided Master Passphrase. Raises ValueError on failure."""
    if not envelope or not envelope.get("wrapped_key") or not envelope.get("salt"):
        raise ValueError("Vault metadata does not contain a passphrase-wrapped key.")
    try:
        salt = base64.b64decode(envelope["salt"])
        iterations = int(envelope.get("iterations") or KDF_ITERATIONS)
        kek = derive_kek_fernet(passphrase, salt, iterations)
        dek = kek.decrypt(envelope["wrapped_key"].encode("ascii")).strip()
        # Validate that dek is a valid Fernet key
        Fernet(dek)
        return dek
    except (InvalidToken, ValueError, KeyError, Exception) as exc:
        raise ValueError("Invalid Master Passphrase.") from exc


def make_key_verifier(dek: bytes) -> str:
    """Create a Fernet token that proves possession of the matching DEK."""
    return Fernet(dek.strip()).encrypt(VERIFIER_PLAINTEXT).decode("ascii")


def check_key_verifier(dek: bytes, verifier_token: str) -> bool:
    """Verify that a candidate DEK matches the stored verifier token."""
    if not dek:
        return False
    try:
        f = Fernet(dek.strip())
        if not verifier_token:
            return True
        plain = f.decrypt(verifier_token.encode("ascii"))
        return plain == VERIFIER_PLAINTEXT
    except Exception:
        return False


def secure_wipe_file(path: str) -> bool:
    """
    Overwrite a file with random bytes, flush to disk, and unlink it
    so raw key material does not linger in filesystem blocks.
    """
    if not os.path.exists(path):
        return True
    try:
        size = max(os.path.getsize(path), 64)
        with open(path, "r+b") as f:
            f.seek(0)
            f.write(os.urandom(size))
            f.flush()
            os.fsync(f.fileno())
            f.seek(0)
            f.write(b"\x00" * size)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    try:
        os.remove(path)
        return True
    except OSError:
        return False


# ── Multi-Backend OS Keyring Drivers ─────────────────────────────────────────

def _try_python_keyring_get() -> tuple[bytes | None, str | None]:
    try:
        import keyring
        from keyring.backends import fail, null
        backend = keyring.get_keyring()
        if isinstance(backend, (fail.Keyring, null.Keyring)):
            return None, None
        val = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        if val:
            return val.strip().encode("ascii"), f"python-keyring ({backend.__class__.__name__})"
    except Exception:
        pass
    return None, None


def _try_python_keyring_set(dek_str: str) -> str | None:
    try:
        import keyring
        from keyring.backends import fail, null
        backend = keyring.get_keyring()
        if isinstance(backend, (fail.Keyring, null.Keyring)):
            return None
        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, dek_str)
        readback = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        if readback and readback.strip() == dek_str:
            return f"python-keyring ({backend.__class__.__name__})"
    except Exception:
        pass
    return None


def _try_python_keyring_delete() -> None:
    try:
        import keyring
        keyring.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except Exception:
        pass


def _try_secret_tool_get() -> tuple[bytes | None, str | None]:
    if not shutil.which("secret-tool"):
        return None, None
    try:
        res = subprocess.run(
            ["secret-tool", "lookup", "service", KEYRING_SERVICE, "account", KEYRING_ACCOUNT],
            capture_output=True,
            text=True,
            timeout=3
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip().encode("ascii"), "Freedesktop Secret Service (secret-tool)"
    except Exception:
        pass
    return None, None


def _try_secret_tool_set(dek_str: str) -> str | None:
    if not shutil.which("secret-tool"):
        return None
    try:
        res = subprocess.run(
            [
                "secret-tool", "store",
                "--label=AMIKO Vault Master Encryption Key",
                "service", KEYRING_SERVICE,
                "account", KEYRING_ACCOUNT
            ],
            input=dek_str,
            capture_output=True,
            text=True,
            timeout=3
        )
        if res.returncode == 0:
            val, _ = _try_secret_tool_get()
            if val and val.decode("ascii", errors="ignore").strip() == dek_str:
                return "Freedesktop Secret Service (secret-tool)"
    except Exception:
        pass
    return None


def _try_secret_tool_delete() -> None:
    if not shutil.which("secret-tool"):
        return
    try:
        subprocess.run(
            ["secret-tool", "clear", "service", KEYRING_SERVICE, "account", KEYRING_ACCOUNT],
            capture_output=True,
            timeout=3
        )
    except Exception:
        pass


# ── Linux Kernel User Keyring (@u) via keyctl CLI or direct libc syscalls ────

def _linux_syscall_archs():
    """Return (SYS_add_key, SYS_keyctl) for the current Linux architecture."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return 248, 250
    if machine in ("aarch64", "arm64"):
        return 217, 219
    if machine.startswith("arm"):
        return 309, 311
    if machine in ("i386", "i686"):
        return 286, 288
    return None, None


def _linux_kernel_keyring_set(dek_bytes: bytes) -> str | None:
    if sys.platform != "linux":
        return None

    # 1. Try keyctl CLI if installed
    if shutil.which("keyctl"):
        try:
            res = subprocess.run(
                ["keyctl", "padd", "user", LINUX_KEY_DESC, "@u"],
                input=dek_bytes,
                capture_output=True,
                timeout=3
            )
            if res.returncode == 0:
                val, _ = _linux_kernel_keyring_get()
                if val == dek_bytes:
                    return "Linux Kernel Keyring (keyctl @u)"
        except Exception:
            pass

    # 2. Direct Linux kernel syscall via ctypes (zero external binary needed)
    sys_add_key, _ = _linux_syscall_archs()
    if sys_add_key is None:
        return None
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        syscall = libc.syscall
        # long add_key(const char *type, const char *description, const void *payload, size_t plen, key_serial_t ringid)
        # KEY_SPEC_USER_KEYRING = -4
        syscall.restype = ctypes.c_long
        key_id = syscall(
            ctypes.c_long(sys_add_key),
            ctypes.c_char_p(b"user"),
            ctypes.c_char_p(LINUX_KEY_DESC.encode("ascii")),
            ctypes.c_char_p(dek_bytes),
            ctypes.c_size_t(len(dek_bytes)),
            ctypes.c_int(-4)
        )
        if key_id > 0:
            val, _ = _linux_kernel_keyring_get()
            if val == dek_bytes:
                return "Linux Kernel Keyring (syscall @u)"
    except Exception:
        pass
    return None


def _linux_kernel_keyring_get() -> tuple[bytes | None, str | None]:
    if sys.platform != "linux":
        return None, None

    # 1. Try keyctl CLI if installed
    if shutil.which("keyctl"):
        try:
            search = subprocess.run(
                ["keyctl", "search", "@u", "user", LINUX_KEY_DESC],
                capture_output=True,
                text=True,
                timeout=3
            )
            if search.returncode == 0 and search.stdout.strip():
                key_id = search.stdout.strip()
                pipe = subprocess.run(
                    ["keyctl", "pipe", key_id],
                    capture_output=True,
                    timeout=3
                )
                if pipe.returncode == 0 and pipe.stdout.strip():
                    return pipe.stdout.strip(), "Linux Kernel Keyring (keyctl @u)"
        except Exception:
            pass

    # 2. Direct Linux kernel syscall via ctypes
    _, sys_keyctl = _linux_syscall_archs()
    if sys_keyctl is None:
        return None, None
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        syscall = libc.syscall
        syscall.restype = ctypes.c_long

        # KEYCTL_SEARCH = 10, KEY_SPEC_USER_KEYRING = -4
        key_id = syscall(
            ctypes.c_long(sys_keyctl),
            ctypes.c_int(10),
            ctypes.c_int(-4),
            ctypes.c_char_p(b"user"),
            ctypes.c_char_p(LINUX_KEY_DESC.encode("ascii")),
            ctypes.c_int(0)
        )
        if key_id <= 0:
            return None, None

        # KEYCTL_READ = 11
        buf = ctypes.create_string_buffer(256)
        n = syscall(
            ctypes.c_long(sys_keyctl),
            ctypes.c_int(11),
            ctypes.c_int(key_id),
            buf,
            ctypes.c_size_t(256)
        )
        if 0 < n <= 256:
            return buf.raw[:n].strip(), "Linux Kernel Keyring (syscall @u)"
    except Exception:
        pass
    return None, None


def _linux_kernel_keyring_delete() -> None:
    if sys.platform != "linux":
        return
    if shutil.which("keyctl"):
        try:
            search = subprocess.run(
                ["keyctl", "search", "@u", "user", LINUX_KEY_DESC],
                capture_output=True,
                text=True,
                timeout=3
            )
            if search.returncode == 0 and search.stdout.strip():
                subprocess.run(["keyctl", "unlink", search.stdout.strip(), "@u"], capture_output=True, timeout=3)
        except Exception:
            pass

    _, sys_keyctl = _linux_syscall_archs()
    if sys_keyctl is not None:
        try:
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            syscall = libc.syscall
            syscall.restype = ctypes.c_long
            key_id = syscall(
                ctypes.c_long(sys_keyctl),
                ctypes.c_int(10),
                ctypes.c_int(-4),
                ctypes.c_char_p(b"user"),
                ctypes.c_char_p(LINUX_KEY_DESC.encode("ascii")),
                ctypes.c_int(0)
            )
            if key_id > 0:
                # KEYCTL_UNLINK = 9
                syscall(ctypes.c_long(sys_keyctl), ctypes.c_int(9), ctypes.c_int(key_id), ctypes.c_int(-4))
        except Exception:
            pass


# ── macOS Keychain Driver ────────────────────────────────────────────────────

def _macos_keychain_get() -> tuple[bytes | None, str | None]:
    if sys.platform != "darwin" or not shutil.which("security"):
        return None, None
    try:
        res = subprocess.run(
            ["security", "find-generic-password", "-s", KEYRING_SERVICE, "-a", KEYRING_ACCOUNT, "-w"],
            capture_output=True,
            text=True,
            timeout=3
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip().encode("ascii"), "macOS Keychain (security)"
    except Exception:
        pass
    return None, None


def _macos_keychain_set(dek_str: str) -> str | None:
    if sys.platform != "darwin" or not shutil.which("security"):
        return None
    try:
        res = subprocess.run(
            ["security", "add-generic-password", "-U", "-s", KEYRING_SERVICE, "-a", KEYRING_ACCOUNT, "-w", dek_str],
            capture_output=True,
            text=True,
            timeout=3
        )
        if res.returncode == 0:
            return "macOS Keychain (security)"
    except Exception:
        pass
    return None


def _macos_keychain_delete() -> None:
    if sys.platform != "darwin" or not shutil.which("security"):
        return
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-s", KEYRING_SERVICE, "-a", KEYRING_ACCOUNT],
            capture_output=True,
            timeout=3
        )
    except Exception:
        pass


# ── Unified OS Keyring Interface ─────────────────────────────────────────────

def store_key_in_os_keyring(dek: bytes) -> str:
    """
    Store the Fernet DEK in the best available OS Keyring backend.
    Returns the human-readable backend name on success, or raises RuntimeError.
    """
    dek_clean = dek.strip()
    dek_str = dek_clean.decode("ascii")

    # 1. Python keyring library
    backend = _try_python_keyring_set(dek_str)
    if backend:
        # Also mirror into Linux kernel keyring if on Linux for service/daemon resilience
        if sys.platform == "linux":
            _linux_kernel_keyring_set(dek_clean)
        return backend

    # 2. Freedesktop Secret Service (secret-tool)
    backend = _try_secret_tool_set(dek_str)
    if backend:
        if sys.platform == "linux":
            _linux_kernel_keyring_set(dek_clean)
        return backend

    # 3. macOS Keychain
    backend = _macos_keychain_set(dek_str)
    if backend:
        return backend

    # 4. Linux Kernel User Keyring (@u)
    backend = _linux_kernel_keyring_set(dek_clean)
    if backend:
        return backend

    raise RuntimeError(
        "No compatible OS Keyring backend is available on this host "
        "(tried python-keyring, Freedesktop secret-tool, macOS Keychain, and Linux Kernel Keyring)."
    )


def load_key_from_os_keyring() -> tuple[bytes | None, str | None]:
    """
    Retrieve the Fernet DEK from any available OS Keyring backend.
    Returns (dek_bytes, backend_name) or (None, None).
    """
    for getter in (
        _try_python_keyring_get,
        _try_secret_tool_get,
        _macos_keychain_get,
        _linux_kernel_keyring_get,
    ):
        dek, backend = getter()
        if dek:
            try:
                Fernet(dek)
                return dek, backend
            except Exception:
                continue
    return None, None


def delete_key_from_os_keyring() -> None:
    """Remove the AMIKO DEK from all OS Keyring backends."""
    _try_python_keyring_delete()
    _try_secret_tool_delete()
    _macos_keychain_delete()
    _linux_kernel_keyring_delete()


def probe_os_keyring_capabilities() -> dict:
    """
    Inspect which OS Keyring backend is available on the current host without clobbering
    any active vault key.
    """
    existing_dek, existing_backend = load_key_from_os_keyring()
    if existing_backend:
        return {
            "available": True,
            "backend": existing_backend,
            "has_stored_key": True
        }

    # Check python-keyring
    try:
        import keyring
        from keyring.backends import fail, null
        b = keyring.get_keyring()
        if not isinstance(b, (fail.Keyring, null.Keyring)):
            return {
                "available": True,
                "backend": f"python-keyring ({b.__class__.__name__})",
                "has_stored_key": False
            }
    except Exception:
        pass

    # Check Linux Kernel Keyring (always available on Linux with CONFIG_KEYS)
    if sys.platform == "linux":
        sys_add, _ = _linux_syscall_archs()
        if shutil.which("keyctl"):
            return {
                "available": True,
                "backend": "Linux Kernel Keyring (keyctl @u)",
                "has_stored_key": False
            }
        if sys_add is not None:
            return {
                "available": True,
                "backend": "Linux Kernel Keyring (syscall @u)",
                "has_stored_key": False
            }

    if sys.platform == "darwin" and shutil.which("security"):
        return {
            "available": True,
            "backend": "macOS Keychain (security)",
            "has_stored_key": False
        }

    if shutil.which("secret-tool"):
        return {
            "available": True,
            "backend": "Freedesktop Secret Service (secret-tool)",
            "has_stored_key": False
        }

    return {
        "available": False,
        "backend": None,
        "has_stored_key": False
    }

