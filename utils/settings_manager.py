"""
AMIKO Settings Manager
Encrypted JSON storage for groups, servers, and theme preferences.
Uses Fernet (AES-128-CBC) symmetric encryption.
"""

import os
import json
import uuid
from cryptography.fernet import Fernet


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json.enc')
KEY_FILE = os.path.join(DATA_DIR, '.amiko_key')

DEFAULT_SETTINGS = {
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
