#!/usr/bin/env python3
"""
AMIKO - Vault Key Protection & OS Keyring CLI Tool
Manage how the AMIKO Vault Data Encryption Key (DEK) is protected at rest:
  - local_file : Store DEK in `data/.amiko_key` (0600) for unattended startup (default).
  - os_keyring : Store DEK in the OS Keyring (Kernel Keyring / Secret Service / Keychain)
                 and securely wipe `data/.amiko_key` from disk.
  - passphrase : Wrap DEK with PBKDF2-HMAC-SHA256 (600,000 iterations) using a Master Passphrase
                 and securely wipe `data/.amiko_key` from disk.

Usage:
    python3 utils/vault_ctl.py --status
    python3 utils/vault_ctl.py --set-passphrase
    python3 utils/vault_ctl.py --set-keyring
    python3 utils/vault_ctl.py --set-local-file
    python3 utils/vault_ctl.py --rotate-key
"""

import os
import sys
import getpass
import argparse

os.environ["AMIKO_CLI_MODE"] = "1"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.config import settings
from core.firewall import FirewallManager
from core.audit import log_audit_event

CLI_ACTOR = {"username": f"cli:{getpass.getuser()}", "role": "admin", "source_ip": "localhost"}


def print_status():
    st = settings.get_vault_status()
    kr = st.get("keyring") or {}
    print("\n== AMIKO ENCRYPTED VAULT STATUS ==")
    print(f"  Runtime State         : {'SEALED (Locked)' if st['sealed'] else 'UNSEALED (Active in RAM)'}")
    print(f"  Protection Mode       : {st['mode']}")
    print(f"  Active DEK Key ID     : #{st['key_id']}")
    print(f"  Disk Key (.amiko_key) : {'PRESENT (0600)' if st['key_file_present'] else 'DECOUPLED / WIPED FROM DISK'}")
    print(f"  Passphrase Envelope   : {'CONFIGURED (' + st['kdf'] + ')' if st['has_recovery_passphrase'] else 'NONE'}")
    print(f"  OS Keyring Available  : {'YES (' + str(kr.get('backend')) + ')' if kr.get('available') else 'NO'}")
    if st.get("configured_keyring_backend"):
        print(f"  Active Keyring Driver : {st['configured_keyring_backend']}")
    if st.get("rotated_at"):
        print(f"  Last Key Rotation     : {st['rotated_at']}")
    print()


def prompt_new_passphrase(label="Master Passphrase", optional=False) -> str:
    while True:
        pw1 = getpass.getpass(f"Enter new {label}{' (leave blank to skip)' if optional else ''}: ").strip()
        if not pw1 and optional:
            return ""
        if len(pw1) < 8:
            print("  [!] Passphrase must be at least 8 characters long.")
            continue
        pw2 = getpass.getpass(f"Confirm new {label}: ").strip()
        if pw1 != pw2:
            print("  [!] Passphrases do not match. Please try again.")
            continue
        return pw1


def main():
    parser = argparse.ArgumentParser(description="AMIKO Vault Key Protection & OS Keyring CLI")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="Show vault key protection and OS keyring status")
    group.add_argument("--set-passphrase", action="store_true", help="Protect vault with a PBKDF2-SHA256 Master Passphrase and wipe .amiko_key")
    group.add_argument("--set-keyring", action="store_true", help="Move vault key to the OS Keyring and wipe .amiko_key")
    group.add_argument("--set-local-file", action="store_true", help="Restore vault key to data/.amiko_key (local_file mode)")
    group.add_argument("--rotate-key", action="store_true", help="Generate a new 256-bit Fernet DEK and re-encrypt all vault stores")
    args = parser.parse_args()

    if args.status:
        print_status()
        return

    # Ensure FirewallManager is initialized so key rotation also re-encrypts firewall.json.enc
    _fw = FirewallManager()

    if not settings.ensure_cli_unsealed():
        sys.exit(1)

    prev_mode = settings.get_vault_status().get("mode")

    try:
        if args.set_passphrase:
            pw = prompt_new_passphrase("Master Passphrase", optional=False)
            st = settings.set_vault_mode("passphrase", passphrase=pw, current_passphrase=pw)
            log_audit_event(
                event_type="VAULT_MODE_CHANGED",
                status="SUCCESS",
                severity="WARNING",
                actor=CLI_ACTOR,
                details={"previous_mode": prev_mode, "new_mode": "passphrase", "key_id": st["key_id"]}
            )
            print("\n[SUCCESS] Vault is now protected by Master Passphrase (PBKDF2-HMAC-SHA256).")
            print("          Local key file 'data/.amiko_key' has been securely wiped from disk.")
            print_status()

        elif args.set_keyring:
            pw = prompt_new_passphrase("Recovery Passphrase (for cold-boot fallback)", optional=True)
            st = settings.set_vault_mode("os_keyring", passphrase=pw)
            log_audit_event(
                event_type="VAULT_MODE_CHANGED",
                status="SUCCESS",
                severity="WARNING",
                actor=CLI_ACTOR,
                details={
                    "previous_mode": prev_mode,
                    "new_mode": "os_keyring",
                    "keyring_backend": st.get("configured_keyring_backend"),
                    "key_id": st["key_id"]
                }
            )
            print(f"\n[SUCCESS] Vault key stored in OS Keyring: {st.get('configured_keyring_backend')}")
            print("          Local key file 'data/.amiko_key' has been securely wiped from disk.")
            print_status()

        elif args.set_local_file:
            st = settings.set_vault_mode("local_file")
            log_audit_event(
                event_type="VAULT_MODE_CHANGED",
                status="SUCCESS",
                severity="WARNING",
                actor=CLI_ACTOR,
                details={"previous_mode": prev_mode, "new_mode": "local_file", "key_id": st["key_id"]}
            )
            print("\n[SUCCESS] Vault key written to 'data/.amiko_key' (0600 permissions).")
            print_status()

        elif args.rotate_key:
            old_id = settings.get_vault_status().get("key_id")
            pw = ""
            if prev_mode == "passphrase":
                pw = getpass.getpass("Confirm current Master Passphrase to re-wrap rotated DEK: ").strip()
            st = settings.rotate_vault_key(passphrase=pw)
            log_audit_event(
                event_type="VAULT_KEY_ROTATED",
                status="SUCCESS",
                severity="WARNING",
                actor=CLI_ACTOR,
                details={"old_key_id": old_id, "new_key_id": st["key_id"], "mode": st["mode"]}
            )
            print(f"\n[SUCCESS] Rotated Vault DEK (#{old_id} -> #{st['key_id']}) and re-encrypted all stores.")
            print_status()

    except Exception as e:
        print(f"\n[ERROR] {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()

