"""
AMIKO Settings Manager
Encrypted JSON storage for groups, servers, and theme preferences.
Uses Fernet (AES-128-CBC) symmetric encryption.
"""

import os
import json
import uuid
from datetime import datetime, timezone
from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash, check_password_hash


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json.enc')
KEY_FILE = os.path.join(DATA_DIR, '.amiko_key')

VALID_ROLES = ("admin", "operator", "viewer")

DEFAULT_SETTINGS = {
    "users": [],
    "groups": [],
    "servers": [],
    "active_theme": "amiko-theme",
    "themes_enabled": {
        "amiko-theme": True,
        "amiko-retro": True,
        "amiko-cyberpunk": True,
        "amiko-batman": True,
        "amiko-barbie": True
    },
    "crt_enabled": True,
    "ui_prefs": {
        "explorer_max_file_mb": 10,
        "winbox_buttons": ["min", "max", "full", "close"]
    }
}


class SettingsManager:
    """Thread-safe encrypted settings manager."""

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self._fernet = Fernet(self._load_or_create_key())
        self._settings = self._load()
        self._ensure_default_admin()

    # --- Key Management ---

    def _load_or_create_key(self):
        if os.path.exists(KEY_FILE):
            with open(KEY_FILE, 'rb') as f:
                return f.read()
        key = Fernet.generate_key()
        with open(KEY_FILE, 'wb') as f:
            f.write(key)
        os.chmod(KEY_FILE, 0o600)
        return key

    # --- Persistence ---

    def _load(self):
        if not os.path.exists(SETTINGS_FILE):
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
        raw = json.dumps(self._settings, indent=2).encode('utf-8')
        encrypted = self._fernet.encrypt(raw)
        with open(SETTINGS_FILE, 'wb') as f:
            f.write(encrypted)

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
        return {k: v for k, v in user.items() if k != "password_hash"}

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

    def create_user(self, username, password, role="operator", allowed_groups=None):
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
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        self._settings.setdefault("users", []).append(user)
        self._save()
        return self._sanitize_user(user)

    def update_user(self, user_id, username=None, password=None, role=None, allowed_groups=None):
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

    def get_servers_for_user(self, user, group_id=None):
        if self.is_full_access_user(user):
            return self.get_servers(group_id)
        if not user:
            return []
        allowed = set(user.get("allowed_groups", []))
        servers = [s for s in self._settings["servers"] if s.get("group_id") in allowed]
        if group_id:
            servers = [s for s in servers if s.get("group_id") == group_id]
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
        allowed_servers = self.get_servers_for_user(user)
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

    def get_servers(self, group_id=None):
        servers = self._settings["servers"]
        if group_id:
            servers = [s for s in servers if s.get("group_id") == group_id]
        return servers

    def create_server(self, group_id, name, hostname, port=22,
                      username="root", password="", notes=""):
        server = {
            "id": str(uuid.uuid4()),
            "group_id": group_id,
            "name": name,
            "hostname": hostname,
            "port": int(port),
            "username": username,
            "password": password,
            "notes": notes
        }
        self._settings["servers"].append(server)
        self._save()
        return server

    def update_server(self, server_id, **kwargs):
        for s in self._settings["servers"]:
            if s["id"] == server_id:
                for key, val in kwargs.items():
                    if key in s and key != "id":
                        s[key] = int(val) if key == "port" else val
                self._save()
                return s
        return None

    def delete_server(self, server_id):
        self._settings["servers"] = [
            s for s in self._settings["servers"] if s["id"] != server_id
        ]
        self._save()
        return True

    def get_quick_list(self):
        """Compact server list for connection dropdown, grouped by category."""
        groups_map = {g["id"]: g["name"] for g in self._settings["groups"]}
        result = []
        for s in self._settings["servers"]:
            result.append({
                "id": s["id"],
                "name": s["name"],
                "hostname": s["hostname"],
                "port": s["port"],
                "username": s["username"],
                "password": s["password"],
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

    # --- CRT Effect ---

    def get_crt_enabled(self):
        return self._settings.get("crt_enabled", True)

    def set_crt_enabled(self, enabled):
        self._settings["crt_enabled"] = bool(enabled)
        self._save()
        return self._settings["crt_enabled"]

    # --- UI Preferences ---

    _UI_DEFAULTS = {
        "explorer_max_file_mb": 10,
        "winbox_buttons": ["min", "max", "full", "close"],
        "ui_font_size": 13,
        "clock_format": "24h",
        "animations_enabled": True,
        "explorer_show_hidden": False,
        "explorer_confirm_delete": True
    }

    def get_ui_prefs(self):
        stored = self._settings.get("ui_prefs", {})
        # Merge with defaults for forward-compatibility
        merged = dict(self._UI_DEFAULTS)
        merged.update(stored)
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
        # Explorer: show hidden files
        if "explorer_show_hidden" in prefs:
            current["explorer_show_hidden"] = bool(prefs["explorer_show_hidden"])
        # Explorer: confirm before delete
        if "explorer_confirm_delete" in prefs:
            current["explorer_confirm_delete"] = bool(prefs["explorer_confirm_delete"])
        self._settings["ui_prefs"] = current
        self._save()
        return current
