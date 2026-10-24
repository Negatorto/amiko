#!/usr/bin/env python3
"""
AMIKO - Emergency 2FA Reset CLI Tool
Allows an administrator with local shell/root access to reset Two-Factor Authentication
for any user (including 'admin') in case of lost device or lockout.

Usage:
    python3 -m utils.reset_2fa <username>
    python3 utils/reset_2fa.py <username>
    python3 utils/reset_2fa.py --list
"""

import sys
import os

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.settings_manager import SettingsManager

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("AMIKO 2FA Emergency Recovery Tool")
        print("Usage:")
        print("  python3 utils/reset_2fa.py <username>    # Reset 2FA for a user")
        print("  python3 utils/reset_2fa.py --list        # List all users and 2FA status")
        sys.exit(0)

    mgr = SettingsManager()
    users = mgr.get_users(safe=False)

    if sys.argv[1] in ("--list", "-l"):
        print(f"\n{'ID':<38} {'USERNAME':<16} {'ROLE':<10} {'2FA STATUS'}")
        print("-" * 75)
        for u in users:
            status = "CONFIGURED (Active)" if u.get("totp_enabled") else "NOT CONFIGURED"
            print(f"{u['id']:<38} {u['username']:<16} {u.get('role', 'unknown'):<10} {status}")
        print()
        sys.exit(0)

    target_username = sys.argv[1].strip()
    target_user = None
    for u in users:
        if u["username"].lower() == target_username.lower():
            target_user = u
            break

    if not target_user:
        print(f"\n[ERROR] User '{target_username}' not found in AMIKO database.\n")
        sys.exit(1)

    ok = mgr.reset_user_totp(target_user["id"])
    if ok:
        print(f"\n[SUCCESS] 2FA registration for user '{target_user['username']}' has been reset.")
        print("The user will be prompted to pair with Google Authenticator on their next login.\n")
    else:
        print(f"\n[ERROR] Failed to reset 2FA for user '{target_user['username']}'.\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
