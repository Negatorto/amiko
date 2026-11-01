#!/usr/bin/env python3
"""
AMIKO - Module Permission Manifest & Integrity Verification CLI Tool

Usage:
  python3 utils/verify_modules.py --status           # Verify all modules and display permission manifests
  python3 utils/verify_modules.py --sign             # Compute SHA-256 digests and update all module.json files
  python3 utils/verify_modules.py --sign <module_id> # Compute SHA-256 digests for a single module
"""

import os
import sys
import argparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ["AMIKO_CLI_MODE"] = "1"

from core.config import MODULES_DIR
from core.plugins import inspect_all_modules, sign_module_manifest
from core.audit import log_audit_event


def cmd_status() -> int:
    modules = inspect_all_modules()
    if not modules:
        print("No modules found in:", MODULES_DIR)
        return 0

    print("=" * 88)
    print(f"{'MODULE ID':<15} {'ENABLED':<9} {'INTEGRITY':<12} {'ALLOWED ROLES':<26} {'CAPABILITIES'}")
    print("-" * 88)

    all_ok = True
    for m in modules:
        mod_id = m["id"]
        enabled = "yes" if m["enabled"] else "no"
        int_status = "VERIFIED" if m["integrity_ok"] else "FAILED"
        if m["enabled"] and not m["integrity_ok"]:
            all_ok = False

        perms = m.get("permissions") or {}
        roles_str = ", ".join(perms.get("allowed_roles", []))
        caps_str = ", ".join(perms.get("capabilities", [])) or "—"

        print(f"{mod_id:<15} {enabled:<9} {int_status:<12} {roles_str:<26} {caps_str}")

        write_eps = perms.get("write_endpoints") or []
        if write_eps:
            print(f"{'':<15} └─ write_endpoints: {', '.join(write_eps)}")

        for issue in m.get("issues", []):
            print(f"{'':<15} [!] {issue}")

    print("=" * 88)
    if all_ok:
        print("Result: All enabled modules passed integrity and manifest verification.")
        return 0
    else:
        print("Result: One or more modules failed integrity verification. Run with --sign to update hashes.")
        return 1


def cmd_sign(target_module: str | None = None) -> int:
    if not os.path.exists(MODULES_DIR):
        print(f"Modules directory not found: {MODULES_DIR}", file=sys.stderr)
        return 1

    if target_module:
        targets = [target_module]
    else:
        targets = [
            d for d in sorted(os.listdir(MODULES_DIR))
            if os.path.isdir(os.path.join(MODULES_DIR, d))
            and not d.startswith(".")
            and os.path.isfile(os.path.join(MODULES_DIR, d, "module.json"))
        ]

    if not targets:
        print("No modules found to sign.")
        return 0

    signed_count = 0
    for mod_id in targets:
        ok, msg, hashes = sign_module_manifest(mod_id)
        if not ok:
            print(f"[ERROR] {msg}", file=sys.stderr)
            return 1
        signed_count += 1
        print(f"[SIGNED] {mod_id}:")
        for fname, digest in hashes.items():
            print(f"         {fname:<15} sha256:{digest}")
        log_audit_event(
            event_type="PLUGIN_MANIFEST_SIGNED",
            status="SUCCESS",
            severity="NOTICE",
            actor={"username": "cli-operator", "role": "admin", "source_ip": "127.0.0.1"},
            target={"module": mod_id},
            details={"files": list(hashes.keys())},
        )

    print(f"\nSuccessfully updated integrity digests for {signed_count} module(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AMIKO Module Permission Manifest & Integrity Verification Tool"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--status",
        action="store_true",
        help="Verify SHA-256 integrity and display permission manifests for all modules (default)",
    )
    group.add_argument(
        "--sign",
        nargs="?",
        const="__ALL__",
        metavar="MODULE_ID",
        help="Compute SHA-256 digests and update module.json for all modules (or a specific MODULE_ID)",
    )

    args = parser.parse_args()
    if args.sign is not None:
        target = None if args.sign == "__ALL__" else args.sign
        return cmd_sign(target)
    return cmd_status()


if __name__ == "__main__":
    sys.exit(main())
