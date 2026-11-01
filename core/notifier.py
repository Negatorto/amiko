"""
AMIKO - Security Email Notifications (NIS2 Art. 21(2)(b) detection, Art. 23 awareness)

Subscribes to the tamper-evident audit trail and emails security-relevant events to a
single security contact (e.g. CISO / SOC mailbox):
  - critical: sent immediately (host key mismatch, audit chain broken, recidive, break-glass CLI)
  - high:     attacks/blocks, aggregated per event type within the cooldown window
  - changes:  sensitive configuration changes, aggregated per event type

Delivery is asynchronous (background worker with retries) so SMTP latency never blocks
requests. Only STARTTLS / implicit TLS with certificate verification is allowed.
Every notification carries the audit event_id/integrity_hash for correlation, and every
delivery outcome is itself audited (NOTIFY_* events are never notified, to avoid loops).
"""

import ssl
import time
import queue
import socket
import smtplib
import threading
from collections import deque
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from core.audit import log_audit_event, register_audit_listener, verify_audit_chain

CRITICAL_EVENTS = {
    "SSH_HOST_KEY_MISMATCH",
    "AUDIT_CHAIN_BROKEN",
    "FAIL2BAN_RECIDIVE_ESCALATION",
    "PLUGIN_INTEGRITY_FAILED",
}
HIGH_EVENTS = {
    "FAIL2BAN_IP_BANNED",
    "AUTH_LOCKOUT",
    "CSRF_ORIGIN_BLOCKED",
    "CSWSH_ORIGIN_BLOCKED",
    "FIREWALL_LOCKOUT_PREVENTED",
    "FIREWALL_EGRESS_BLOCKED",
}
CHANGE_EVENTS = {
    "USER_CREATED", "USER_UPDATED", "USER_DELETED",
    "2FA_RESET", "2FA_RECOVERY_CODE_USED",
    "BACKUP_RESTORED",
    "FIREWALL_SETTINGS_UPDATED", "FIREWALL_KERNEL_SYNC_UPDATED",
    "FIREWALL_RULE_CREATED", "FIREWALL_RULE_UPDATED", "FIREWALL_RULE_DELETED", "FIREWALL_RULE_MOVED",
    "FIREWALL_SERVICE_CREATED", "FIREWALL_SERVICE_UPDATED", "FIREWALL_SERVICE_DELETED",
    "FAIL2BAN_SETTINGS_UPDATED", "FAIL2BAN_JAIL_UPDATED", "FAIL2BAN_WHITELIST_ADDED",
    "FAIL2BAN_IP_UNBANNED", "FAIL2BAN_BANS_FLUSHED",
    "PLUGIN_MANIFEST_SIGNED",
}
CATEGORY_LABELS = {"critical": "CRITICAL", "high": "HIGH", "changes": "CHANGE"}

SMTP_TIMEOUT = 15
RETRY_DELAYS = (5, 30, 120)       # back-off before each retry (non-blocking, re-queued)
DIGEST_MAX_LINES = 50
CHAIN_CHECK_INTERVAL = 3600       # hourly audit hash-chain verification
TICK = 5                          # worker wake-up interval (digest flushing)

NIS2_FOOTER = (
    "NIS2 reminder: if this event is part of a significant incident, Art. 23 of Directive (EU) 2022/2555\n"
    "(D.Lgs. 138/2024) requires an early warning to CSIRT Italia within 24 hours of becoming aware of it,\n"
    "an incident notification within 72 hours and a final report within one month."
)


def classify(record: dict):
    """Return the notification category of an audit record, or None if it is not notifiable."""
    event_type = record.get("event_type", "")
    if event_type.startswith("NOTIFY_"):
        return None
    if (record.get("actor") or {}).get("role") == "break-glass":
        return "critical"  # any emergency CLI action on the firewall
    if event_type in CRITICAL_EVENTS or record.get("severity") == "CRITICAL":
        return "critical"
    if event_type in HIGH_EVENTS:
        return "high"
    if event_type in CHANGE_EVENTS:
        return "changes"
    return None


def _short_target(record):
    target = record.get("target") or {}
    if target.get("hostname"):
        return f"{target['hostname']}:{target.get('port', '')}".rstrip(":")
    return str(target.get("resource") or "")


def _actor_line(record):
    actor = record.get("actor") or {}
    return f"{actor.get('username', 'anonymous')} ({actor.get('role', 'none')}) from {actor.get('source_ip', 'unknown')}"


def _format_value(value):
    text = str(value)
    return text if len(text) <= 300 else text[:297] + "..."


class EmailNotifier:
    """Asynchronous, rate-limited email dispatcher fed by the audit trail."""

    def __init__(self, settings_manager):
        self.settings = settings_manager
        self.instance = socket.gethostname()
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._windows = {}        # event_type -> {"until": ts, "pending": [records], "category": str}
        self.history = deque(maxlen=50)
        self._retries = []        # [(due_ts, attempt, cfg_override, subject, body, event_ids)]
        self._worker_started = False
        self.retry_enabled = True     # disabled in short-lived CLI processes (no blocking on SMTP outages)
        self._last_chain_check = 0.0
        self._chain_alerted = False

    # --- Intake ---

    def on_audit_record(self, record):
        category = classify(record)
        if category is None:
            return
        cfg = self.settings.get_smtp_config(safe=False)
        if not cfg["enabled"] or not cfg["categories"].get(category, False):
            return
        self._queue.put(("event", record, category, None))

    def notify_config_change(self, old_cfg, record):
        """Tell the previous recipient that notification settings changed (an attacker silencing alerts)."""
        if old_cfg.get("enabled") and old_cfg.get("recipient"):
            self._queue.put(("event", record, "critical", old_cfg))

    # --- Message building ---

    def _subject(self, category, text):
        return f"[AMIKO@{self.instance}][{CATEGORY_LABELS[category]}] {text}"[:200]

    def _single_message(self, record, category):
        target = _short_target(record)
        lines = [
            "AMIKO Security Notification",
            "=" * 27,
            "",
            f"Instance       : {self.instance}",
            f"Category       : {CATEGORY_LABELS[category]}",
            f"Event          : {record.get('event_type')}",
            f"Severity       : {record.get('severity')}",
            f"Status         : {record.get('status')}",
            f"Time (UTC)     : {record.get('timestamp')}",
            f"Actor          : {_actor_line(record)}",
            f"Target         : {target or '-'}",
        ]
        details = record.get("details") or {}
        if details:
            lines += ["", "Details:"]
            lines += [f"  - {k}: {_format_value(v)}" for k, v in details.items()]
        lines += [
            "",
            f"Audit event ID : {record.get('event_id')}",
            f"Integrity hash : {record.get('integrity_hash')}",
            "",
            "Verify this event in AMIKO > Settings > Audit (SHA-256 hash-chained trail).",
        ]
        if category == "critical":
            lines += ["", NIS2_FOOTER]
        subject = self._subject(category, f"{record.get('event_type')}{' - ' + target if target else ''}")
        return subject, "\n".join(lines)

    def _digest_message(self, event_type, category, records, cooldown_min):
        lines = [
            "AMIKO Security Notification (digest)",
            "=" * 36,
            "",
            f"Instance : {self.instance}",
            f"Category : {CATEGORY_LABELS[category]}",
            f"Event    : {event_type}",
            f"Count    : {len(records)} additional event(s) in the last {cooldown_min} minute(s)",
            "",
        ]
        for rec in records[:DIGEST_MAX_LINES]:
            target = _short_target(rec)
            lines.append(f"  {rec.get('timestamp', '')[:19]}Z  {_actor_line(rec)}"
                         f"{'  -> ' + target if target else ''}  [{rec.get('event_id')}]")
        if len(records) > DIGEST_MAX_LINES:
            lines.append(f"  ... and {len(records) - DIGEST_MAX_LINES} more (see AMIKO > Settings > Audit)")
        subject = self._subject(category, f"{len(records)} x {event_type} in the last {cooldown_min} min")
        return subject, "\n".join(lines)

    # --- Delivery ---

    def _send(self, cfg, subject, body):
        """Deliver one message over STARTTLS or implicit TLS. Raises on failure."""
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg["from_addr"]
        msg["To"] = cfg["recipient"]
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain=cfg["from_addr"].split("@")[-1])
        msg["X-AMIKO-Instance"] = self.instance
        msg.set_content(body)

        context = ssl.create_default_context()
        if cfg["security"] == "ssl":
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=SMTP_TIMEOUT, context=context)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=SMTP_TIMEOUT)
        try:
            if cfg["security"] == "starttls":
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
            if cfg["username"]:
                server.login(cfg["username"], cfg["password"])
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                server.close()

    def _deliver(self, cfg_override, subject, body, event_ids, retry=True, attempt=0):
        """
        Try one delivery. On failure the message is re-queued with back-off instead of sleeping,
        so an SMTP outage never delays other (e.g. critical) notifications.
        cfg_override=None means "use the live configuration", also for retries.
        """
        cfg = cfg_override or self.settings.get_smtp_config(safe=False)
        try:
            self._send(cfg, subject, body)
            self.history.appendleft({"ts": time.time(), "subject": subject, "status": "SENT", "error": ""})
            log_audit_event(
                event_type="NOTIFY_SENT",
                status="SUCCESS",
                severity="INFO",
                actor={"username": "notifier", "role": "system", "source_ip": "localhost"},
                target={"resource": f"mailto:{cfg['recipient']}"},
                details={"subject": subject, "event_ids": event_ids[:20], "attempt": attempt + 1}
            )
            return True, ""
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
        delays = RETRY_DELAYS if (retry and self.retry_enabled) else ()
        if attempt < len(delays):
            with self._lock:
                self._retries.append((time.time() + delays[attempt], attempt + 1, cfg_override,
                                      subject, body, event_ids))
            return False, last_error
        self.history.appendleft({"ts": time.time(), "subject": subject, "status": "FAILED", "error": last_error})
        log_audit_event(
            event_type="NOTIFY_FAILED",
            status="FAILURE",
            severity="ERROR",
            actor={"username": "notifier", "role": "system", "source_ip": "localhost"},
            target={"resource": f"mailto:{cfg['recipient']}"},
            details={"subject": subject, "error": last_error, "event_ids": event_ids[:20]}
        )
        return False, last_error

    def send_test(self):
        """Synchronous test email (no retries) so the UI can show the SMTP error immediately."""
        cfg = self.settings.get_smtp_config(safe=False)
        if not (cfg["host"] and cfg["from_addr"] and cfg["recipient"]):
            return False, "Configure host, sender and recipient first."
        body = (
            "AMIKO Security Notification - test message\n\n"
            f"Instance : {self.instance}\n"
            f"Time     : {datetime.now(timezone.utc).isoformat()}\n\n"
            "If you received this email, SMTP notifications are correctly configured."
        )
        return self._deliver(None, self._subject("changes", "Test notification"), body, [], retry=False)

    # --- Processing ---

    def _process_retries(self):
        with self._lock:
            now = time.time()
            due = [r for r in self._retries if r[0] <= now]
            self._retries = [r for r in self._retries if r[0] > now]
        for _, attempt, cfg_override, subject, body, event_ids in due:
            if cfg_override is None and not self.settings.get_smtp_config(safe=False)["enabled"]:
                continue
            self._deliver(cfg_override, subject, body, event_ids, attempt=attempt)

    def _process_event(self, record, category, cfg_override):
        cfg = cfg_override or self.settings.get_smtp_config(safe=False)
        if not cfg_override and not cfg["enabled"]:
            return
        event_type = record.get("event_type", "")
        if category != "critical" and not cfg_override:
            with self._lock:
                window = self._windows.get(event_type)
                if window and time.time() < window["until"]:
                    window["pending"].append(record)  # aggregated into the next digest
                    return
                self._windows[event_type] = {
                    "until": time.time() + cfg["cooldown_min"] * 60,
                    "pending": [],
                    "category": category
                }
        subject, body = self._single_message(record, category)
        self._deliver(cfg_override, subject, body, [record.get("event_id")])

    def _flush_digests(self, force=False):
        cfg = self.settings.get_smtp_config(safe=False)
        due = []
        with self._lock:
            now = time.time()
            for event_type, window in list(self._windows.items()):
                if not force and now < window["until"]:
                    continue
                if window["pending"]:
                    due.append((event_type, window["category"], window["pending"]))
                    # keep throttling: a new window starts with the digest
                    self._windows[event_type] = {"until": now + cfg["cooldown_min"] * 60,
                                                 "pending": [], "category": window["category"]}
                else:
                    self._windows.pop(event_type, None)
        if not cfg["enabled"]:
            return
        for event_type, category, records in due:
            subject, body = self._digest_message(event_type, category, records, cfg["cooldown_min"])
            self._deliver(None, subject, body, [r.get("event_id") for r in records])

    def _check_audit_chain(self):
        if time.time() - self._last_chain_check < CHAIN_CHECK_INTERVAL:
            return
        self._last_chain_check = time.time()
        if not self.settings.get_smtp_config(safe=False)["enabled"]:
            return
        chain = verify_audit_chain()
        if chain["valid"]:
            self._chain_alerted = False
        elif not self._chain_alerted:
            self._chain_alerted = True
            log_audit_event(
                event_type="AUDIT_CHAIN_BROKEN",
                status="FAILURE",
                severity="CRITICAL",
                actor={"username": "notifier", "role": "system", "source_ip": "localhost"},
                target={"resource": "audit.log"},
                details={"corrupted_line": chain.get("corrupted_line"), "reason": chain.get("reason", "")}
            )

    def process_pending(self, block_seconds=0):
        """Drain the queue (used by the worker, and synchronously by short-lived CLI processes)."""
        deadline = time.time() + block_seconds
        while True:
            timeout = max(0, deadline - time.time())
            try:
                item = self._queue.get(timeout=timeout) if timeout else self._queue.get_nowait()
            except queue.Empty:
                return
            _, record, category, cfg_override = item
            try:
                self._process_event(record, category, cfg_override)
            except Exception as e:
                print(f"[AMIKO Notifier] Error processing {record.get('event_type')}: {e}")

    def flush(self):
        """Send everything queued, including pending digests (CLI exit path)."""
        self.process_pending()
        self._flush_digests(force=True)

    def start_worker(self):
        if self._worker_started:
            return
        self._worker_started = True

        def loop():
            while True:
                try:
                    self.process_pending(block_seconds=TICK)
                    self._process_retries()
                    self._flush_digests()
                    self._check_audit_chain()
                except Exception as e:
                    print(f"[AMIKO Notifier] Worker error: {e}")

        threading.Thread(target=loop, name="amiko-email-notifier", daemon=True).start()


_notifier = None


def get_notifier():
    global _notifier
    if _notifier is None:
        from core.config import settings
        _notifier = EmailNotifier(settings)
        register_audit_listener(_notifier.on_audit_record)
    return _notifier


def init_notifier(app=None, start_worker=True):
    notifier = get_notifier()
    if start_worker:
        notifier.start_worker()
    else:
        notifier.retry_enabled = False
    return notifier
