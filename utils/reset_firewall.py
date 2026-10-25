#!/usr/bin/env python3
"""
AMIKO - Emergency Firewall & Fail2Ban Recovery CLI Tool (break-glass)
For administrators with local shell access, to recover from a firewall lock-out.
Changes are picked up by a running AMIKO server within ~1 second (hot reload)
and every action is recorded in the tamper-evident audit trail.

Usage:
    python3 utils/reset_firewall.py --status
    python3 utils/reset_firewall.py --unban <ip_address>
    python3 utils/reset_firewall.py --disable
    python3 utils/reset_firewall.py --flush-bans
    python3 utils/reset_firewall.py --disable-fail2ban
"""

import os
import sys
import getpass
import argparse
from datetime import datetime

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.firewall import FirewallManager
from core.audit import log_audit_event
from core.notifier import init_notifier

CLI_ACTOR = {"username": f"cli:{getpass.getuser()}", "role": "break-glass", "source_ip": "localhost"}


def audit(event_type, target=None, details=None):
    log_audit_event(event_type=event_type, status="SUCCESS", severity="WARNING",
                    actor=CLI_ACTOR, target=target or {"resource": "firewall"}, details=details or {})


def fmt_ts(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"


def print_status(fw: FirewallManager):
    state = fw.get_state()
    rules = state["firewall"]["rules"]
    f2b = state["fail2ban"]
    ks = state["kernel_sync"]

    print("\n== AMIKO FIREWALL ==")
    print(f"  Rule engine     : {'ENABLED' if state['firewall']['enabled'] else 'DISABLED'}")
    print(f"  INPUT policy    : {state['firewall']['input_policy']}")
    print(f"  OUTPUT policy   : {state['firewall']['output_policy']}")
    print(f"  Kernel sync     : {'ENABLED' if ks['enabled'] else 'DISABLED'}"
          f" (backend: {ks.get('backend') or 'n/a'})")
    if rules:
        print(f"\n  {'#':<3} {'ID':<11} {'CHAIN':<7} {'IFACE':<6} {'SOURCE':<20} {'DEST':<20} "
              f"{'PROTO':<5} {'SERVICE':<12} {'STATE':<11} TARGET")
        for i, r in enumerate(rules, start=1):
            flag = "" if r.get("enabled", True) else " (disabled)"
            print(f"  {i:<3} {r['id']:<11} {r['chain']:<7} {r['interface']:<6} {r['source'][:20]:<20} "
                  f"{r['destination'][:20]:<20} {r['protocol']:<5} {r['service'][:12]:<12} "
                  f"{r.get('state', 'ANY'):<11} {r['target']}{flag}")
    else:
        print("\n  No firewall rules defined.")

    print("\n== FAIL2BAN ==")
    print(f"  Status          : {'ENABLED' if f2b['enabled'] else 'DISABLED'}")
    print(f"  Whitelist       : {', '.join(f2b['ignore_ips']) or '-'}")
    for name, j in f2b["jails"].items():
        ban_time = "permanent" if j["ban_time"] == -1 else f"{j['ban_time']} min"
        print(f"  [{'x' if j['enabled'] else ' '}] {name:<12} max_retry={j['max_retry']:<4} "
              f"find_time={j['find_time']} min  ban_time={ban_time}")

    bans = state["bans"]
    print(f"\n== BANNED IPs ({len(bans)}) ==")
    for b in bans:
        expires = "permanent" if not b.get("expires_at") else fmt_ts(b["expires_at"])
        print(f"  {b['ip']:<40} jail={b['jail']:<12} banned={fmt_ts(b['banned_at'])}  "
              f"expires={expires}  reason={b.get('reason', '')}")
    print()


def main():
    parser = argparse.ArgumentParser(description="AMIKO Firewall & Fail2Ban Emergency Recovery Tool")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="Show firewall rules, jails and banned IPs")
    group.add_argument("--unban", metavar="IP", help="Immediately unban an IP address")
    group.add_argument("--disable", action="store_true", help="Emergency disable of the firewall rule engine")
    group.add_argument("--flush-bans", action="store_true", help="Remove all active bans")
    group.add_argument("--disable-fail2ban", action="store_true", help="Disable Fail2Ban automatic banning")
    args = parser.parse_args()

    fw = FirewallManager()
    # No background worker in a short-lived process: notifications are flushed before exit
    notifier = init_notifier(start_worker=False)

    if args.status:
        print_status(fw)
    elif args.unban:
        if fw.unban(args.unban, audit=False):
            audit("FAIL2BAN_IP_UNBANNED", {"resource": f"ip:{args.unban}"}, {"initiated_by": "cli"})
            print(f"\n[SUCCESS] {args.unban} has been unbanned.\n")
        else:
            print(f"\n[INFO] {args.unban} is not currently banned.\n")
            sys.exit(1)
    elif args.disable:
        fw.update_settings({"enabled": False}, operator_ctx=None)
        audit("FIREWALL_SETTINGS_UPDATED", details={"enabled": False, "initiated_by": "cli"})
        print("\n[SUCCESS] Firewall rule engine DISABLED. Rules are kept and can be re-enabled from the UI.")
        print("Active Fail2Ban bans are still enforced: use --unban <ip> or --flush-bans if needed.\n")
    elif args.flush_bans:
        count = fw.flush_bans()
        audit("FAIL2BAN_BANS_FLUSHED", details={"removed": count, "initiated_by": "cli"})
        print(f"\n[SUCCESS] {count} ban(s) removed.\n")
    elif args.disable_fail2ban:
        fw.update_fail2ban({"enabled": False})
        audit("FAIL2BAN_SETTINGS_UPDATED", details={"enabled": False, "initiated_by": "cli"})
        print("\n[SUCCESS] Fail2Ban automatic banning DISABLED (existing bans kept).\n")

    notifier.flush()


if __name__ == "__main__":
    main()
