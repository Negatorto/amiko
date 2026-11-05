"""
AMIKO Settings Manager
Encrypted JSON storage for groups, servers, and theme preferences.
Uses Fernet (AES-128-CBC) symmetric encryption.
"""

import os
import re
import json
import uuid
import base64
import hashlib
import io
import getpass
import secrets
from datetime import datetime, timezone
from cryptography.fernet import Fernet, InvalidToken
from werkzeug.security import generate_password_hash, check_password_hash
import pyotp
import qrcode
import qrcode.image.svg

from utils.vault_key import (
    VAULT_MODES,
    KDF_ITERATIONS,
    key_fingerprint,
    wrap_dek_with_passphrase,
    unwrap_dek_with_passphrase,
    make_key_verifier,
    check_key_verifier,
    secure_wipe_file,
    store_key_in_os_keyring,
    load_key_from_os_keyring,
    delete_key_from_os_keyring,
    probe_os_keyring_capabilities,
)


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json.enc')
KEY_FILE = os.path.join(DATA_DIR, '.amiko_key')
VAULT_META_FILE = os.path.join(DATA_DIR, 'vault_meta.json')
NOTES_ENC_FILE = os.path.join(DATA_DIR, 'notes_data.enc')

VALID_ROLES = ("admin", "operator", "viewer")

SMTP_SECURITY_MODES = ("starttls", "ssl")
NOTIFY_CATEGORIES = ("critical", "high", "changes")
_EMAIL_RE = re.compile(r"^[^@\s<>\"',;]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")

DEFAULT_SMTP = {
    "enabled": False,
    "host": "",
    "port": 587,
    "security": "starttls",
    "username": "",
    "password": "",
    "from_addr": "",
    "recipient": "",
    "categories": {"critical": True, "high": True, "changes": True},
    "cooldown_min": 10
}

DEFAULT_SETTINGS = {
    "users": [],
    "groups": [],
    "servers": [],
    "active_theme": "amiko-theme",
    "themes_enabled": {
        "amiko-theme": True,
        "amiko-matrix": True,
        "amiko-cyberpunk": True,
        "amiko-batman": True,
        "amiko-barbie": True,
        "amiko-classic": True,
        "amiko-synthwave": True,
        "amiko-pacman": True,
        "amiko-pipboy": True,
        "amiko-spaceinvaders": True,
        "amiko-highcontrast": True,
        "amiko-nordic": True
    },
    "crt_enabled": True,
    "bg_effect_enabled": True,
    "smtp": DEFAULT_SMTP,
    "ui_prefs": {
        "explorer_max_file_mb": 10,
        "winbox_buttons": ["min", "max", "full", "close"]
    }
}


class SettingsManager:
    """Thread-safe encrypted settings manager with Envelope Encryption & OS Keyring support."""

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self._dek = None
        self._fernet = None
        self._unseal_listeners = []
        self._rotate_listeners = []
        self._vault_meta = self._load_or_init_vault_meta()
        self._settings = json.loads(json.dumps(DEFAULT_SETTINGS))
        self._try_auto_unseal()

    # --- Vault Key Management & Envelope Protection ---

    @property
    def is_sealed(self) -> bool:
        """Return True if the vault DEK is not currently loaded in RAM."""
        return self._fernet is None

    def register_unseal_listener(self, callback):
        """Register a callback(fernet) invoked whenever the vault is unsealed."""
        if callback not in self._unseal_listeners:
            self._unseal_listeners.append(callback)

    def register_rotate_listener(self, callback):
        """Register a callback(old_fernet, new_fernet) invoked when the vault DEK is rotated."""
        if callback not in self._rotate_listeners:
            self._rotate_listeners.append(callback)

    def get_fernet(self) -> Fernet | None:
        return self._fernet

    def get_flask_secret_key(self) -> str:
        """
        Return a persistent secret key for signing Flask session cookies,
        safe to call at startup even while the vault is sealed.
        """
        env_key = os.environ.get("AMIKO_SECRET_KEY")
        if env_key:
            return env_key
        cookie_secret = self._vault_meta.get("cookie_secret")
        if not cookie_secret:
            cookie_secret = secrets.token_hex(32)
            self._vault_meta["cookie_secret"] = cookie_secret
            self._save_vault_meta()
        return cookie_secret

    def _load_or_init_vault_meta(self) -> dict:
        now_iso = datetime.now(timezone.utc).isoformat()
        if os.path.exists(VAULT_META_FILE):
            try:
                with open(VAULT_META_FILE, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                if isinstance(meta, dict):
                    meta.setdefault("version", 1)
                    meta.setdefault("mode", "local_file")
                    if not meta.get("cookie_secret"):
                        meta["cookie_secret"] = secrets.token_hex(32)
                        self._save_vault_meta(meta)
                    return meta
            except Exception as e:
                print(f"[SettingsManager] Warning: could not read vault_meta.json ({e})")

        meta = {
            "version": 1,
            "mode": "local_file",
            "created_at": now_iso,
            "updated_at": now_iso,
            "cookie_secret": secrets.token_hex(32),
        }
        self._save_vault_meta(meta)
        return meta

    def _save_vault_meta(self, meta: dict = None):
        target = meta if meta is not None else self._vault_meta
        tmp = f"{VAULT_META_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(target, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, VAULT_META_FILE)

    def _load_or_create_key(self) -> bytes:
        """Legacy helper for local_file mode."""
        if os.path.exists(KEY_FILE):
            with open(KEY_FILE, 'rb') as f:
                return f.read().strip()
        key = Fernet.generate_key()
        with open(KEY_FILE, 'wb') as f:
            f.write(key)
        os.chmod(KEY_FILE, 0o600)
        return key

    def _activate_dek(self, dek: bytes):
        """Load the DEK into memory, decrypt settings, and notify listeners."""
        dek_clean = dek.strip()
        self._dek = dek_clean
        self._fernet = Fernet(dek_clean)

        # Ensure key_id and key_verifier are recorded in vault_meta.json
        changed = False
        fp = key_fingerprint(dek_clean)
        if self._vault_meta.get("key_id") != fp:
            self._vault_meta["key_id"] = fp
            changed = True
        if not self._vault_meta.get("key_verifier"):
            self._vault_meta["key_verifier"] = make_key_verifier(dek_clean)
            changed = True
        if changed:
            self._save_vault_meta()

        self._settings = self._load()
        self._ensure_default_admin()

        for cb in list(self._unseal_listeners):
            try:
                cb(self._fernet)
            except Exception as e:
                print(f"[SettingsManager] Unseal listener error: {e}")

    def _try_auto_unseal(self):
        """
        Attempt to unseal the vault automatically at startup using:
          1. AMIKO_VAULT_KEY environment variable (raw Fernet key)
          2. AMIKO_MASTER_PASSPHRASE environment variable (passphrase unwrap)
          3. Configured protection mode ('local_file' or 'os_keyring')
        If mode is 'passphrase' (and no env var is set), the vault remains SEALED.
        """
        verifier = self._vault_meta.get("key_verifier")

        # 1. Explicit raw key in environment variable
        env_key = (os.environ.get("AMIKO_VAULT_KEY") or "").strip()
        if env_key:
            raw_bytes = env_key.encode("ascii")
            if check_key_verifier(raw_bytes, verifier):
                self._activate_dek(raw_bytes)
                return

        # 2. Explicit master passphrase in environment variable
        env_pass = (os.environ.get("AMIKO_MASTER_PASSPHRASE") or "").strip()
        if env_pass and self._vault_meta.get("wrapped_key"):
            try:
                dek = unwrap_dek_with_passphrase(self._vault_meta, env_pass)
                if check_key_verifier(dek, verifier):
                    self._activate_dek(dek)
                    return
            except ValueError as e:
                print(f"[SettingsManager] AMIKO_MASTER_PASSPHRASE failed to unseal vault: {e}")

        mode = self._vault_meta.get("mode", "local_file")

        # 3a. Local Disk Key mode (default)
        if mode == "local_file":
            dek = self._load_or_create_key()
            self._activate_dek(dek)
            return

        # 3b. OS Keyring mode
        if mode == "os_keyring":
            dek, backend_name = load_key_from_os_keyring()
            if dek and check_key_verifier(dek, verifier):
                if backend_name and self._vault_meta.get("keyring_backend") != backend_name:
                    self._vault_meta["keyring_backend"] = backend_name
                    self._save_vault_meta()
                self._activate_dek(dek)
                return
            # Fallback: if .amiko_key still exists on disk from an interrupted migration
            if os.path.exists(KEY_FILE):
                with open(KEY_FILE, "rb") as f:
                    disk_dek = f.read().strip()
                if check_key_verifier(disk_dek, verifier):
                    try:
                        b_name = store_key_in_os_keyring(disk_dek)
                        self._vault_meta["keyring_backend"] = b_name
                        self._save_vault_meta()
                        secure_wipe_file(KEY_FILE)
                    except Exception:
                        pass
                    self._activate_dek(disk_dek)
                    return
            print("[SettingsManager] OS Keyring did not return an active key. Vault is starting in SEALED state.")
            return

        # 3c. Master Passphrase mode -> starts SEALED in RAM until unsealed via Web UI or CLI
        print("[SettingsManager] Vault protection mode is 'passphrase'. Starting in SEALED state.")

    def ensure_cli_unsealed(self) -> bool:
        """
        Helper for interactive CLI tools (reset_2fa.py, reset_firewall.py, vault_ctl.py).
        If the vault is sealed, prompts the operator on the terminal for the Master Passphrase.
        """
        if not self.is_sealed:
            return True
        if not self._vault_meta.get("wrapped_key"):
            print("[ERROR] Vault is sealed and no recovery/master passphrase envelope is available.")
            return False
        for attempt in range(1, 4):
            try:
                pw = getpass.getpass(f"[AMIKO Vault Sealed] Enter Master Passphrase ({attempt}/3): ")
            except (EOFError, KeyboardInterrupt):
                print()
                return False
            try:
                if self.unseal(pw):
                    return True
            except ValueError as e:
                print(f"  -> {e}")
        return False

    def unseal(self, passphrase: str) -> bool:
        """
        Unseal a locked vault in memory using the Master Passphrase (or recovery passphrase).
        Raises ValueError if the passphrase is invalid.
        """
        pw = (passphrase or "").strip()
        if not pw:
            raise ValueError("Master Passphrase is required.")
        dek = unwrap_dek_with_passphrase(self._vault_meta, pw)
        verifier = self._vault_meta.get("key_verifier")
        if not check_key_verifier(dek, verifier):
            raise ValueError("Decrypted key does not match the vault verifier.")

        # If mode is os_keyring (e.g. cold boot cleared kernel keyring), restore key into OS keyring automatically
        if self._vault_meta.get("mode") == "os_keyring":
            try:
                b_name = store_key_in_os_keyring(dek)
                self._vault_meta["keyring_backend"] = b_name
                self._save_vault_meta()
            except Exception as e:
                print(f"[SettingsManager] Could not re-populate OS keyring on unseal: {e}")

        self._activate_dek(dek)
        return True

    def seal(self) -> bool:
        """
        Wipe the DEK from RAM and lock the vault immediately.
        Only permitted when a passphrase envelope or OS keyring can unseal it again.
        """
        if self.is_sealed:
            return True
        mode = self._vault_meta.get("mode", "local_file")
        if mode == "local_file" and not self._vault_meta.get("wrapped_key"):
            raise ValueError(
                "Cannot seal the vault while in 'Local Key File' mode without a Master Passphrase. "
                "Switch protection mode to 'Master Passphrase' first."
            )
        # If sealing in os_keyring mode, require a recovery passphrase envelope or remove volatile key
        if mode == "os_keyring":
            if not self._vault_meta.get("wrapped_key"):
                raise ValueError(
                    "To manually seal an OS Keyring vault, configure a recovery Master Passphrase first."
                )
            delete_key_from_os_keyring()

        self._dek = None
        self._fernet = None
        self._settings = json.loads(json.dumps(DEFAULT_SETTINGS))
        return True

    def get_vault_status(self) -> dict:
        """Return safe telemetry about the encrypted vault and key protection layer."""
        keyring_info = probe_os_keyring_capabilities()
        return {
            "sealed": self.is_sealed,
            "mode": self._vault_meta.get("mode", "local_file"),
            "key_id": key_fingerprint(self._dek) if not self.is_sealed else self._vault_meta.get("key_id", "sealed"),
            "key_file_present": os.path.exists(KEY_FILE),
            "has_recovery_passphrase": bool(self._vault_meta.get("wrapped_key")),
            "keyring": keyring_info,
            "configured_keyring_backend": self._vault_meta.get("keyring_backend"),
            "kdf": self._vault_meta.get("kdf", "PBKDF2-HMAC-SHA256"),
            "iterations": self._vault_meta.get("iterations", KDF_ITERATIONS),
            "updated_at": self._vault_meta.get("updated_at"),
            "rotated_at": self._vault_meta.get("rotated_at"),
            "env_override": bool(os.environ.get("AMIKO_VAULT_KEY") or os.environ.get("AMIKO_MASTER_PASSPHRASE")),
        }

    def set_vault_mode(self, mode: str, passphrase: str = "", current_passphrase: str = "") -> dict:
        """
        Transition the vault DEK between 'local_file', 'passphrase', and 'os_keyring' modes.
        Securely wipes `data/.amiko_key` when decoupling from local disk.
        """
        if self.is_sealed or not self._dek:
            raise ValueError("Vault must be unsealed before changing protection mode.")
        if mode not in VAULT_MODES:
            raise ValueError(f"Invalid vault mode '{mode}'. Choose from: {', '.join(VAULT_MODES)}.")

        # If currently in passphrase mode, verify current_passphrase when provided or required
        if self._vault_meta.get("mode") == "passphrase" and self._vault_meta.get("wrapped_key"):
            cur_pw = (current_passphrase or passphrase or "").strip()
            if not cur_pw:
                raise ValueError("Current Master Passphrase is required to change vault protection mode.")
            unwrap_dek_with_passphrase(self._vault_meta, cur_pw)

        now_iso = datetime.now(timezone.utc).isoformat()
        pw = (passphrase or "").strip()

        if mode == "passphrase":
            if len(pw) < 8:
                raise ValueError("Master Passphrase must be at least 8 characters long.")
            envelope = wrap_dek_with_passphrase(self._dek, pw)
            self._vault_meta.update(envelope)
            self._vault_meta["mode"] = "passphrase"
            self._vault_meta["keyring_backend"] = None
            self._vault_meta["key_id"] = key_fingerprint(self._dek)
            self._vault_meta["key_verifier"] = make_key_verifier(self._dek)
            self._vault_meta["updated_at"] = now_iso
            self._save_vault_meta()
            delete_key_from_os_keyring()
            secure_wipe_file(KEY_FILE)

        elif mode == "os_keyring":
            backend_name = store_key_in_os_keyring(self._dek)
            if pw:
                if len(pw) < 8:
                    raise ValueError("Recovery Passphrase must be at least 8 characters long.")
                envelope = wrap_dek_with_passphrase(self._dek, pw)
                self._vault_meta.update(envelope)
            self._vault_meta["mode"] = "os_keyring"
            self._vault_meta["keyring_backend"] = backend_name
            self._vault_meta["key_id"] = key_fingerprint(self._dek)
            self._vault_meta["key_verifier"] = make_key_verifier(self._dek)
            self._vault_meta["updated_at"] = now_iso
            self._save_vault_meta()
            secure_wipe_file(KEY_FILE)

        elif mode == "local_file":
            with open(KEY_FILE, "wb") as f:
                f.write(self._dek)
            os.chmod(KEY_FILE, 0o600)
            delete_key_from_os_keyring()
            for k in ("kdf", "iterations", "salt", "wrapped_key", "keyring_backend"):
                self._vault_meta.pop(k, None)
            self._vault_meta["mode"] = "local_file"
            self._vault_meta["key_id"] = key_fingerprint(self._dek)
            self._vault_meta["key_verifier"] = make_key_verifier(self._dek)
            self._vault_meta["updated_at"] = now_iso
            self._save_vault_meta()

        return self.get_vault_status()

    def rotate_vault_key(self, passphrase: str = "") -> dict:
        """
        Generate a brand-new 256-bit Fernet DEK and atomically re-encrypt:
          - data/settings.json.enc
          - data/notes_data.enc
          - data/firewall.json.enc (via registered rotate listeners)
        Then persist the new DEK in the active protection layer.
        """
        if self.is_sealed or not self._dek or not self._fernet:
            raise ValueError("Vault must be unsealed before rotating the encryption key.")

        mode = self._vault_meta.get("mode", "local_file")
        pw = (passphrase or "").strip()

        if mode == "passphrase":
            if not pw:
                raise ValueError("Master Passphrase is required to re-wrap the newly rotated key.")
            # Verify passphrase against current envelope before rotating so a typo never locks the admin out
            unwrap_dek_with_passphrase(self._vault_meta, pw)
        elif mode == "os_keyring" and self._vault_meta.get("wrapped_key") and pw:
            unwrap_dek_with_passphrase(self._vault_meta, pw)

        old_fernet = self._fernet
        new_dek = Fernet.generate_key()
        new_fernet = Fernet(new_dek)

        # 1. Re-encrypt notes_data.enc if present
        if os.path.exists(NOTES_ENC_FILE):
            with open(NOTES_ENC_FILE, "rb") as f:
                notes_plain = old_fernet.decrypt(f.read())
            notes_tmp = f"{NOTES_ENC_FILE}.tmp"
            with open(notes_tmp, "wb") as f:
                f.write(new_fernet.encrypt(notes_plain))
            os.chmod(notes_tmp, 0o600)
            os.replace(notes_tmp, NOTES_ENC_FILE)

        # 2. Switch settings cipher and re-encrypt settings.json.enc
        self._dek = new_dek
        self._fernet = new_fernet
        self._save()

        # 3. Notify external encrypted stores (e.g. FirewallManager)
        for cb in list(self._rotate_listeners):
            try:
                cb(old_fernet, new_fernet)
            except Exception as e:
                print(f"[SettingsManager] Rotate listener error: {e}")

        # 4. Persist new DEK in the active key protection backend
        now_iso = datetime.now(timezone.utc).isoformat()
        if mode == "local_file":
            with open(KEY_FILE, "wb") as f:
                f.write(new_dek)
            os.chmod(KEY_FILE, 0o600)
        elif mode == "os_keyring":
            b_name = store_key_in_os_keyring(new_dek)
            self._vault_meta["keyring_backend"] = b_name
            if pw:
                self._vault_meta.update(wrap_dek_with_passphrase(new_dek, pw))
            else:
                # If no passphrase was supplied during rotation in os_keyring mode, clear stale envelope
                for k in ("salt", "wrapped_key"):
                    self._vault_meta.pop(k, None)
            secure_wipe_file(KEY_FILE)
        elif mode == "passphrase":
            self._vault_meta.update(wrap_dek_with_passphrase(new_dek, pw))
            secure_wipe_file(KEY_FILE)

        self._vault_meta["key_id"] = key_fingerprint(new_dek)
        self._vault_meta["key_verifier"] = make_key_verifier(new_dek)
        self._vault_meta["rotated_at"] = now_iso
        self._vault_meta["updated_at"] = now_iso
        self._save_vault_meta()

        return self.get_vault_status()

    # --- Persistence ---

    def _load(self):
        if self.is_sealed or not os.path.exists(SETTINGS_FILE):
            return json.loads(json.dumps(DEFAULT_SETTINGS))
        try:
            with open(SETTINGS_FILE, 'rb') as f:
                encrypted = f.read()
            decrypted = self._fernet.decrypt(encrypted)
            data = json.loads(decrypted.decode('utf-8'))
            # Merge with defaults for forward-compatibility
            for key, val in DEFAULT_SETTINGS.items():
                if key not in data:
                    data[key] = val
            return data
        except Exception as e:
            print(f"[SettingsManager] Error loading settings, resetting: {e}")
            return json.loads(json.dumps(DEFAULT_SETTINGS))

    def _save(self):
        if self.is_sealed:
            raise RuntimeError("Cannot write to encrypted settings while the vault is sealed.")
        raw = json.dumps(self._settings, indent=2).encode('utf-8')
        encrypted = self._fernet.encrypt(raw)
        tmp = f"{SETTINGS_FILE}.tmp"
        with open(tmp, 'wb') as f:
            f.write(encrypted)
        os.chmod(tmp, 0o600)
        os.replace(tmp, SETTINGS_FILE)

    # --- Users & RBAC (Least Privilege) ---

    def _ensure_default_admin(self):
        if "users" not in self._settings or not self._settings["users"]:
            default_admin = {
                "id": str(uuid.uuid4()),
                "username": "admin",
                "password_hash": generate_password_hash("admin"),
                "role": "admin",
                "allowed_groups": ["*"],
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            self._settings["users"] = [default_admin]
            self._save()

    @staticmethod
    def _sanitize_user(user):
        if not user:
            return None
        sanitized = {
            k: v for k, v in user.items()
            if k not in (
                "password_hash",
                "totp_secret",
                "totp_recovery_codes",
                "pending_totp_secret",
                "pending_recovery_codes"
            )
        }
        sanitized["totp_enabled"] = bool(user.get("totp_enabled", False))
        return sanitized

    def get_users(self, safe=True):
        users = self._settings.get("users", [])
        if safe:
            return [self._sanitize_user(u) for u in users]
        return users

    def get_user_by_id(self, user_id, safe=True):
        for u in self._settings.get("users", []):
            if u["id"] == user_id:
                return self._sanitize_user(u) if safe else u
        return None

    def get_user_by_username(self, username, safe=True):
        uname = (username or "").strip().lower()
        for u in self._settings.get("users", []):
            if u["username"].lower() == uname:
                return self._sanitize_user(u) if safe else u
        return None

    def verify_user(self, username, password):
        user = self.get_user_by_username(username, safe=False)
        if not user or not password:
            return None
        if check_password_hash(user["password_hash"], password):
            return self._sanitize_user(user)
        return None

    def create_user(self, username, password, role="operator", allowed_groups=None, can_manage_firewall=False):
        uname = (username or "").strip()
        if not uname or not password:
            raise ValueError("Username and password are required.")
        if role not in VALID_ROLES:
            raise ValueError(f"Invalid role '{role}'. Allowed: {', '.join(VALID_ROLES)}")
        if self.get_user_by_username(uname):
            raise ValueError(f"User '{uname}' already exists.")

        groups = ["*"] if role == "admin" else list(allowed_groups or [])
        user = {
            "id": str(uuid.uuid4()),
            "username": uname,
            "password_hash": generate_password_hash(password),
            "role": role,
            "allowed_groups": groups,
            # Delegated permission: only meaningful for operators (admins have it implicitly)
            "can_manage_firewall": role == "operator" and bool(can_manage_firewall),
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        self._settings.setdefault("users", []).append(user)
        self._save()
        return self._sanitize_user(user)

    def update_user(self, user_id, username=None, password=None, role=None, allowed_groups=None,
                    can_manage_firewall=None):
        users = self._settings.get("users", [])
        target = None
        for u in users:
            if u["id"] == user_id:
                target = u
                break
        if not target:
            return None

        # Prevent demoting the last admin
        if role is not None and role != "admin" and target.get("role") == "admin":
            admin_count = sum(1 for u in users if u.get("role") == "admin")
            if admin_count <= 1:
                raise ValueError("Cannot change role of the last remaining admin user.")

        if username is not None:
            uname = username.strip()
            if not uname:
                raise ValueError("Username cannot be empty.")
            existing = self.get_user_by_username(uname)
            if existing and existing["id"] != user_id:
                raise ValueError(f"Username '{uname}' is already taken.")
            target["username"] = uname

        if role is not None:
            if role not in VALID_ROLES:
                raise ValueError(f"Invalid role '{role}'.")
            target["role"] = role

        if password:
            target["password_hash"] = generate_password_hash(password)

        if target["role"] == "admin":
            target["allowed_groups"] = ["*"]
        elif allowed_groups is not None:
            target["allowed_groups"] = list(allowed_groups)

        if target["role"] != "operator":
            target["can_manage_firewall"] = False
        elif can_manage_firewall is not None:
            target["can_manage_firewall"] = bool(can_manage_firewall)

        self._save()
        return self._sanitize_user(target)

    def delete_user(self, user_id):
        users = self._settings.get("users", [])
        target = next((u for u in users if u["id"] == user_id), None)
        if not target:
            return False

        if target.get("role") == "admin":
            admin_count = sum(1 for u in users if u.get("role") == "admin")
            if admin_count <= 1:
                raise ValueError("Cannot delete the last remaining admin user.")

        self._settings["users"] = [u for u in users if u["id"] != user_id]
        self._save()
        return True

    # --- Two-Factor Authentication (2FA / TOTP RFC 6238) ---

    def init_user_totp_setup(self, user_id):
        """
        Prepare 2FA enrollment for a user.
        Generates or reuses a pending TOTP secret, a standard otpauth URI,
        a crisp SVG QR Code for Google Authenticator, and 5 one-time emergency recovery codes.
        """
        user = self.get_user_by_id(user_id, safe=False)
        if not user:
            return None

        # Re-use existing pending secret if present, or generate a fresh one
        secret = user.get("pending_totp_secret")
        if not secret:
            secret = pyotp.random_base32()
            user["pending_totp_secret"] = secret

        # Generate fresh recovery codes for this setup attempt
        raw_recovery_codes = [
            f"{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"
            for _ in range(5)
        ]
        user["pending_recovery_codes"] = raw_recovery_codes
        self._save()

        # Build standard RFC 6238 otpauth URI (compatible with Google Authenticator, Bitwarden, etc.)
        totp = pyotp.TOTP(secret)
        otpauth_uri = totp.provisioning_uri(name=user["username"], issuer_name="AMIKO")

        # Generate SVG QR Code (lossless, lightweight, zero binary dependencies)
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
            image_factory=qrcode.image.svg.SvgPathImage
        )
        qr.add_data(otpauth_uri)
        qr.make(fit=True)

        buf = io.BytesIO()
        qr.make_image().save(buf)
        svg_xml = buf.getvalue().decode("utf-8")
        svg_b64 = base64.b64encode(svg_xml.encode("utf-8")).decode("ascii")
        qr_data_url = f"data:image/svg+xml;base64,{svg_b64}"

        return {
            "secret": secret,
            "otpauth_uri": otpauth_uri,
            "qr_data_url": qr_data_url,
            "recovery_codes": raw_recovery_codes,
            "username": user["username"]
        }

    def confirm_user_totp(self, user_id, token):
        """
        Verify the first 6-digit code against the pending secret to confirm enrollment.
        Only when confirmed is totp_enabled set to True and recovery codes activated.
        """
        user = self.get_user_by_id(user_id, safe=False)
        if not user:
            return False

        secret = user.get("pending_totp_secret") or user.get("totp_secret")
        if not secret:
            return False

        totp = pyotp.TOTP(secret)
        clean_token = str(token).strip()
        if not totp.verify(clean_token, valid_window=1):
            return False

        user["totp_secret"] = secret
        user["totp_enabled"] = True
        user.pop("pending_totp_secret", None)

        pending_codes = user.pop("pending_recovery_codes", None)
        if pending_codes:
            # Hash recovery codes so plaintext codes are never stored on disk
            user["totp_recovery_codes"] = [
                hashlib.sha256(c.replace("-", "").strip().upper().encode("utf-8")).hexdigest()
                for c in pending_codes
            ]

        self._save()
        return True

    def verify_user_totp(self, user_id, token_or_code):
        """
        Verify a 6-digit TOTP token or an emergency recovery code.
        Returns (success: bool, method: 'totp' | 'recovery_code' | None).
        """
        user = self.get_user_by_id(user_id, safe=False)
        if not user or not user.get("totp_enabled"):
            return False, None

        cleaned = str(token_or_code).strip()

        # 1. Check standard 6-digit TOTP token
        if len(cleaned) == 6 and cleaned.isdigit():
            secret = user.get("totp_secret")
            if secret and pyotp.TOTP(secret).verify(cleaned, valid_window=1):
                return True, "totp"

        # 2. Check emergency one-time recovery codes
        clean_recovery = cleaned.replace("-", "").strip().upper()
        hashed_attempt = hashlib.sha256(clean_recovery.encode("utf-8")).hexdigest()
        active_codes = user.get("totp_recovery_codes", [])
        if hashed_attempt in active_codes:
            # Burn the used recovery code
            active_codes.remove(hashed_attempt)
            user["totp_recovery_codes"] = active_codes
            self._save()
            return True, "recovery_code"

        return False, None

    def reset_user_totp(self, user_id):
        """
        Reset/clear 2FA registration for a user (Admin action or CLI break-glass).
        Clears the stored secret, recovery codes, and marks 2FA as unconfigured.
        """
        user = self.get_user_by_id(user_id, safe=False)
        if not user:
            return False

        user["totp_secret"] = None
        user["totp_enabled"] = False
        user["totp_recovery_codes"] = []
        user.pop("pending_totp_secret", None)
        user.pop("pending_recovery_codes", None)
        self._save()
        return True

    def is_full_access_user(self, user):
        if not user:
            return False
        return user.get("role") == "admin" or "*" in user.get("allowed_groups", [])

    def get_groups_for_user(self, user):
        all_groups = self.get_groups()
        if self.is_full_access_user(user):
            return all_groups
        if not user:
            return []
        allowed = set(user.get("allowed_groups", []))
        return [g for g in all_groups if g["id"] in allowed]

    @staticmethod
    def _sanitize_server(server):
        """Strip plaintext password and private_key before sending server metadata to the frontend."""
        if not server:
            return None
        sanitized = {k: v for k, v in server.items() if k not in ("password", "private_key")}
        sanitized["has_password"] = bool(server.get("password") or server.get("private_key"))
        sanitized["has_private_key"] = bool(server.get("private_key"))
        return sanitized

    def get_server_by_id(self, server_id, safe=True):
        if not server_id:
            return None
        for s in self._settings.get("servers", []):
            if s["id"] == server_id:
                return self._sanitize_server(s) if safe else s
        return None

    def get_servers_for_user(self, user, group_id=None, safe=True):
        if self.is_full_access_user(user):
            return self.get_servers(group_id, safe=safe)
        if not user:
            return []
        allowed = set(user.get("allowed_groups", []))
        servers = [s for s in self._settings["servers"] if s.get("group_id") in allowed]
        if group_id:
            servers = [s for s in servers if s.get("group_id") == group_id]
        if safe:
            return [self._sanitize_server(s) for s in servers]
        return servers

    def get_quick_list_for_user(self, user):
        full_list = self.get_quick_list()
        if self.is_full_access_user(user):
            return full_list
        if not user:
            return []
        allowed = set(user.get("allowed_groups", []))
        return [s for s in full_list if s.get("group_id") in allowed]

    def can_user_access_server(self, user, hostname=None, port=None, server_id=None, group_id=None):
        if self.is_full_access_user(user):
            return True
        if not user:
            return False
        allowed_groups = set(user.get("allowed_groups", []))
        if group_id and group_id in allowed_groups:
            return True
        allowed_servers = self.get_servers_for_user(user, safe=False)
        if server_id and any(s["id"] == server_id for s in allowed_servers):
            return True
        if hostname:
            port_int = int(port) if port is not None else 22
            for s in allowed_servers:
                if s["hostname"] == hostname and int(s.get("port", 22)) == port_int:
                    return True
            for s in allowed_servers:
                if s["hostname"] == hostname:
                    return True
        return False

    def resolve_connection_credentials(self, data, user=None, ssh_sessions=None):
        """
        Resolve (hostname, port, username, password, private_key, server_id, group_id) on the backend
        so plaintext passwords and SSH private keys never need to travel to or from the browser.
        """
        data = data or {}
        session_id = data.get("session_id")
        server_id = data.get("server_id")
        hostname = (data.get("hostname") or "").strip()
        port = int(data.get("port") or 22)
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        private_key = data.get("private_key") or ""
        group_id = data.get("group_id")

        server_name = data.get("server_name") or ""

        # 1. Resolve from active server-side SSH session if session_id is valid
        if session_id and ssh_sessions and ssh_sessions in (ssh_sessions,) and session_id in ssh_sessions:
            s_data = ssh_sessions[session_id]
            hostname = hostname or s_data.get("hostname", "")
            port = int(data.get("port") or s_data.get("port", 22))
            username = username or s_data.get("username", "")
            password = password or s_data.get("password", "")
            private_key = private_key or s_data.get("private_key", "")
            server_id = server_id or s_data.get("server_id")
            server_name = server_name or s_data.get("server_name", "")
            group_id = group_id or s_data.get("group_id")

        # 2. Resolve from encrypted vault by server_id
        if server_id:
            srv = self.get_server_by_id(server_id, safe=False)
            if srv and (user is None or self.can_user_access_server(user, server_id=server_id)):
                hostname = hostname or srv.get("hostname", "")
                port = int(data.get("port") or srv.get("port", 22))
                username = username or srv.get("username", "")
                if not password:
                    password = srv.get("password", "")
                if not private_key:
                    private_key = srv.get("private_key", "")
                server_name = server_name or srv.get("name", "")
                group_id = group_id or srv.get("group_id")

        # 3. Fallback: match by (hostname, port, username) in user's allowed vault servers
        if hostname and (not (password or private_key) or not server_name):
            candidates = self.get_servers_for_user(user, safe=False) if user else self.get_servers(safe=False)
            for srv in candidates:
                if (
                    srv.get("hostname") == hostname
                    and int(srv.get("port", 22)) == port
                    and (not username or srv.get("username") == username)
                ):
                    username = username or srv.get("username", "")
                    if not password:
                        password = srv.get("password", "")
                    if not private_key:
                        private_key = srv.get("private_key", "")
                    server_id = server_id or srv.get("id")
                    server_name = server_name or srv.get("name", "")
                    group_id = group_id or srv.get("group_id")
                    break

        if not hostname or not username:
            return None

        return {
            "hostname": hostname,
            "port": port,
            "username": username,
            "password": password,
            "private_key": private_key,
            "server_id": server_id,
            "server_name": server_name,
            "group_id": group_id
        }

    # --- Groups CRUD ---

    def get_groups(self):
        return sorted(self._settings["groups"], key=lambda g: g.get("order", 999))

    def create_group(self, name, icon="📁"):
        group = {
            "id": str(uuid.uuid4()),
            "name": name,
            "icon": icon,
            "order": len(self._settings["groups"])
        }
        self._settings["groups"].append(group)
        self._save()
        return group

    def update_group(self, group_id, name=None, icon=None, order=None):
        for g in self._settings["groups"]:
            if g["id"] == group_id:
                if name is not None:
                    g["name"] = name
                if icon is not None:
                    g["icon"] = icon
                if order is not None:
                    g["order"] = order
                self._save()
                return g
        return None

    def delete_group(self, group_id):
        self._settings["groups"] = [
            g for g in self._settings["groups"] if g["id"] != group_id
        ]
        # Also delete servers belonging to this group
        self._settings["servers"] = [
            s for s in self._settings["servers"] if s.get("group_id") != group_id
        ]
        # Also remove group_id from users' allowed_groups
        for u in self._settings.get("users", []):
            if "allowed_groups" in u and group_id in u["allowed_groups"]:
                u["allowed_groups"] = [gid for gid in u["allowed_groups"] if gid != group_id]
        self._save()
        return True

    # --- Servers CRUD ---

    def get_servers(self, group_id=None, safe=True):
        servers = self._settings["servers"]
        if group_id:
            servers = [s for s in servers if s.get("group_id") == group_id]
        if safe:
            return [self._sanitize_server(s) for s in servers]
        return servers

    def create_server(self, group_id, name, hostname, port=22,
                      username="root", password="", private_key="", notes=""):
        server = {
            "id": str(uuid.uuid4()),
            "group_id": group_id,
            "name": name,
            "hostname": hostname,
            "port": int(port),
            "username": username,
            "password": password,
            "private_key": private_key,
            "notes": notes
        }
        self._settings["servers"].append(server)
        self._save()
        return self._sanitize_server(server)

    def update_server(self, server_id, **kwargs):
        for s in self._settings["servers"]:
            if s["id"] == server_id:
                for key, val in kwargs.items():
                    if key in ("name", "hostname", "port", "username", "password", "private_key", "notes", "group_id"):
                        if key in ("password", "private_key"):
                            if val == "__CLEAR__":
                                s[key] = ""
                            elif val is not None and str(val).strip() != "":
                                s[key] = val
                        elif key == "port":
                            s["port"] = int(val)
                        else:
                            s[key] = val
                self._save()
                return self._sanitize_server(s)
        return None

    def delete_server(self, server_id):
        self._settings["servers"] = [
            s for s in self._settings["servers"] if s["id"] != server_id
        ]
        self._save()
        return True

    def get_quick_list(self):
        """Compact server list for connection dropdown, grouped by category (Zero-Password)."""
        groups_map = {g["id"]: g["name"] for g in self._settings["groups"]}
        result = []
        for s in self._settings["servers"]:
            result.append({
                "id": s["id"],
                "name": s["name"],
                "hostname": s["hostname"],
                "port": s["port"],
                "username": s["username"],
                "has_password": bool(s.get("password") or s.get("private_key")),
                "has_private_key": bool(s.get("private_key")),
                "group_name": groups_map.get(s.get("group_id"), "Ungrouped"),
                "group_id": s.get("group_id", "")
            })
        return result

    # --- Themes ---

    def get_themes_status(self):
        return {
            "active_theme": self._settings["active_theme"],
            "themes_enabled": self._settings["themes_enabled"]
        }

    def set_active_theme(self, theme_name):
        self._settings["active_theme"] = theme_name
        self._save()
        return True

    def toggle_theme(self, theme_name):
        if theme_name in self._settings["themes_enabled"]:
            current = self._settings["themes_enabled"][theme_name]
            # Cannot disable the active theme
            if current and self._settings["active_theme"] == theme_name:
                return None
            self._settings["themes_enabled"][theme_name] = not current
            self._save()
            return self._settings["themes_enabled"][theme_name]
        return None

    # --- CRT Effect & Live Background ---

    def get_crt_enabled(self):
        return self._settings.get("crt_enabled", True)

    def set_crt_enabled(self, enabled):
        self._settings["crt_enabled"] = bool(enabled)
        self._save()
        return self._settings["crt_enabled"]

    def get_bg_effect_enabled(self):
        return self._settings.get("bg_effect_enabled", True)

    def set_bg_effect_enabled(self, enabled):
        self._settings["bg_effect_enabled"] = bool(enabled)
        self._save()
        return self._settings["bg_effect_enabled"]

    # --- UI Preferences ---

    _UI_DEFAULTS = {
        "explorer_max_file_mb": 10,
        "winbox_buttons": ["min", "max", "full", "close"],
        "ui_font_size": 13,
        "clock_format": "24h",
        "animations_enabled": True,
        "bg_effect_enabled": True,
        "explorer_show_hidden": False,
        "explorer_confirm_delete": True
    }

    def get_ui_prefs(self):
        stored = self._settings.get("ui_prefs", {})
        # Merge with defaults for forward-compatibility
        merged = dict(self._UI_DEFAULTS)
        merged.update(stored)
        merged["bg_effect_enabled"] = self.get_bg_effect_enabled()
        return merged

    def set_ui_prefs(self, prefs: dict):
        current = self.get_ui_prefs()
        # Validate explorer_max_file_mb (1–20 MB)
        if "explorer_max_file_mb" in prefs:
            mb = int(prefs["explorer_max_file_mb"])
            current["explorer_max_file_mb"] = max(1, min(20, mb))
        # Validate winbox_buttons (must be subset of known buttons; 'close' always forced)
        if "winbox_buttons" in prefs:
            allowed = {"min", "max", "full"}
            btns = [b for b in prefs["winbox_buttons"] if b in allowed]
            if "close" not in btns:
                btns.append("close")
            current["winbox_buttons"] = btns
        # UI font size (11–17 px)
        if "ui_font_size" in prefs:
            current["ui_font_size"] = max(11, min(17, int(prefs["ui_font_size"])))
        # Clock format (24h / 12h)
        if "clock_format" in prefs:
            current["clock_format"] = "12h" if prefs["clock_format"] == "12h" else "24h"
        # Animations toggle
        if "animations_enabled" in prefs:
            current["animations_enabled"] = bool(prefs["animations_enabled"])
        # Live desktop background toggle
        if "bg_effect_enabled" in prefs:
            bg_val = bool(prefs["bg_effect_enabled"])
            current["bg_effect_enabled"] = bg_val
            self._settings["bg_effect_enabled"] = bg_val
        # Explorer: show hidden files
        if "explorer_show_hidden" in prefs:
            current["explorer_show_hidden"] = bool(prefs["explorer_show_hidden"])
        # Explorer: confirm before delete
        if "explorer_confirm_delete" in prefs:
            current["explorer_confirm_delete"] = bool(prefs["explorer_confirm_delete"])
        self._settings["ui_prefs"] = current
        self._save()
        return current

    # --- SMTP Email Notifications ---

    def get_smtp_config(self, safe=True):
        """SMTP settings merged with defaults. safe=True strips the password (Zero-Password frontend)."""
        stored = self._settings.get("smtp") or {}
        cfg = json.loads(json.dumps(DEFAULT_SMTP))
        cfg.update({k: v for k, v in stored.items() if k != "categories"})
        cfg["categories"].update(stored.get("categories") or {})
        if safe:
            cfg["has_password"] = bool(cfg.pop("password", ""))
        return cfg

    def update_smtp_config(self, data: dict):
        """Validate and persist SMTP settings. Empty password keeps the stored one, '__CLEAR__' removes it."""
        cfg = self.get_smtp_config(safe=False)

        for key in ("host", "username", "from_addr", "recipient"):
            if key in data:
                val = str(data[key] or "").strip()
                if "\r" in val or "\n" in val:
                    raise ValueError(f"Invalid characters in {key}.")
                cfg[key] = val
        if "password" in data:
            pw = data["password"] or ""
            if pw == "__CLEAR__":
                cfg["password"] = ""
            elif str(pw) != "":
                cfg["password"] = str(pw)
        if "port" in data:
            try:
                port = int(data["port"])
            except (TypeError, ValueError):
                raise ValueError("SMTP port must be a number.")
            if not 1 <= port <= 65535:
                raise ValueError("SMTP port must be between 1 and 65535.")
            cfg["port"] = port
        if "security" in data:
            sec = str(data["security"]).lower()
            if sec not in SMTP_SECURITY_MODES:
                raise ValueError("Plain-text SMTP is not allowed: choose STARTTLS or SSL/TLS.")
            cfg["security"] = sec
        if "cooldown_min" in data:
            try:
                cooldown = int(data["cooldown_min"])
            except (TypeError, ValueError):
                raise ValueError("Cooldown must be a number of minutes.")
            if not 1 <= cooldown <= 1440:
                raise ValueError("Cooldown must be between 1 and 1440 minutes.")
            cfg["cooldown_min"] = cooldown
        if isinstance(data.get("categories"), dict):
            for cat in NOTIFY_CATEGORIES:
                if cat in data["categories"]:
                    cfg["categories"][cat] = bool(data["categories"][cat])
        if "enabled" in data:
            cfg["enabled"] = bool(data["enabled"])

        if cfg["host"] and not _HOSTNAME_RE.match(cfg["host"]):
            raise ValueError("Invalid SMTP host.")
        for key, label in (("from_addr", "Sender address"), ("recipient", "Recipient address")):
            if cfg[key] and not _EMAIL_RE.match(cfg[key]):
                raise ValueError(f"{label} is not a valid email address.")
        if cfg["enabled"] and not (cfg["host"] and cfg["from_addr"] and cfg["recipient"]):
            raise ValueError("Host, sender and recipient are required to enable notifications.")

        self._settings["smtp"] = cfg
        self._save()
        return self.get_smtp_config(safe=True)

    # --- Optional Backup & Restore (Passphrase-Encrypted or Plain JSON) ---

    @staticmethod
    def _derive_backup_fernet(passphrase: str, salt: bytes) -> Fernet:
        key_bytes = hashlib.pbkdf2_hmac(
            "sha256",
            passphrase.encode("utf-8"),
            salt,
            600000,
            dklen=32
        )
        return Fernet(base64.urlsafe_b64encode(key_bytes))

    def export_backup_bundle(self, passphrase: str = "", notes: list = None, known_hosts_raw: str = "") -> dict:
        """
        Create a portable backup snapshot of settings, servers, groups, users, notes, and known_hosts.
        If passphrase is provided, the payload is encrypted with PBKDF2-HMAC-SHA256 + Fernet.
        If passphrase is empty, the payload is exported as plain JSON (optional encryption).
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        payload = {
            "settings": self._settings,
            "notes": notes if isinstance(notes, list) else [],
            "known_hosts": known_hosts_raw or ""
        }
        pw = (passphrase or "").strip()
        if pw:
            salt = os.urandom(16)
            cipher = self._derive_backup_fernet(pw, salt)
            raw_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            token = cipher.encrypt(raw_bytes).decode("ascii")
            return {
                "amiko_backup": True,
                "version": "0.7.1",
                "created_at": now_iso,
                "encrypted": True,
                "kdf": "PBKDF2-HMAC-SHA256-600000",
                "salt": base64.b64encode(salt).decode("ascii"),
                "ciphertext": token
            }
        return {
            "amiko_backup": True,
            "version": "0.7.1",
            "created_at": now_iso,
            "encrypted": False,
            "data": payload
        }

    def import_backup_bundle(self, bundle: dict, passphrase: str = "") -> dict:
        """
        Restore a backup snapshot created by export_backup_bundle().
        Returns {"notes": [...], "known_hosts": "..."} for caller to persist auxiliary stores.
        """
        if not isinstance(bundle, dict) or not bundle.get("amiko_backup"):
            raise ValueError("Invalid AMIKO backup file format.")

        if bundle.get("encrypted"):
            pw = (passphrase or "").strip()
            if not pw:
                raise ValueError("This backup is encrypted. Please provide the backup passphrase.")
            try:
                salt = base64.b64decode(bundle["salt"])
                cipher = self._derive_backup_fernet(pw, salt)
                decrypted = cipher.decrypt(bundle["ciphertext"].encode("ascii"))
                payload = json.loads(decrypted.decode("utf-8"))
            except (InvalidToken, KeyError, ValueError, Exception):
                raise ValueError("Decryption failed: invalid passphrase or corrupted backup file.")
        else:
            payload = bundle.get("data")
            if not isinstance(payload, dict):
                raise ValueError("Invalid backup payload.")

        restored_settings = payload.get("settings")
        if not isinstance(restored_settings, dict) or "servers" not in restored_settings:
            raise ValueError("Backup is missing core settings structure.")

        # Merge with defaults to ensure all keys exist
        merged = dict(DEFAULT_SETTINGS)
        merged.update(restored_settings)
        self._settings = merged
        self._ensure_default_admin()
        self._save()

        return {
            "notes": payload.get("notes") if isinstance(payload.get("notes"), list) else [],
            "known_hosts": payload.get("known_hosts") if isinstance(payload.get("known_hosts"), str) else ""
        }
