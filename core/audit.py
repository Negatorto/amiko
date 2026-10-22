"""
AMIKO - Structured Tamper-Evident Audit Logger & Brute-Force Protection
Implements:
  1. Append-only JSONL audit trail (data/audit.log) with SHA-256 hash chaining
     so any tampering, deletion, or modification of past log lines is detectable.
  2. Login rate-limiting & progressive brute-force lockout per username and IP.
"""

import os
import json
import uuid
import time
import hashlib
import threading
from datetime import datetime, timezone
from flask import request, has_request_context

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
AUDIT_LOG_FILE = os.path.join(DATA_DIR, "audit.log")
GENESIS_HASH = "0" * 64

_audit_lock = threading.Lock()
_last_hash_cache = None

# ── Brute-Force / Login Rate Limiter ─────────────────────────────────────────
MAX_FAILED_ATTEMPTS = 5
ATTEMPT_WINDOW_SECONDS = 15 * 60   # 15 minutes window
LOCKOUT_DURATION_SECONDS = 5 * 60  # 5 minutes lockout

_rate_limit_lock = threading.Lock()
# key -> {"failures": [timestamp, ...], "locked_until": float}
_login_attempts = {}


def check_login_lockout(username: str, source_ip: str = "") -> tuple[bool, int]:
    """
    Check if the username or (username, source_ip) is currently locked out.
    Returns (is_locked, remaining_seconds).
    """
    now = time.time()
    uname = (username or "").strip().lower()
    keys = [f"user:{uname}", f"ip:{source_ip}:{uname}"] if source_ip else [f"user:{uname}"]

    with _rate_limit_lock:
        max_remaining = 0
        for key in keys:
            entry = _login_attempts.get(key)
            if not entry:
                continue
            locked_until = entry.get("locked_until", 0)
            if locked_until > now:
                rem = int(locked_until - now) + 1
                if rem > max_remaining:
                    max_remaining = rem
        if max_remaining > 0:
            return True, max_remaining
    return False, 0


def record_login_attempt(username: str, source_ip: str = "", success: bool = False) -> tuple[bool, int, int]:
    """
    Record a login attempt.
    Returns (just_locked_out, remaining_lockout_seconds, failure_count).
    """
    now = time.time()
    uname = (username or "").strip().lower()
    keys = [f"user:{uname}", f"ip:{source_ip}:{uname}"] if source_ip else [f"user:{uname}"]

    with _rate_limit_lock:
        if success:
            for key in keys:
                _login_attempts.pop(key, None)
            return False, 0, 0

        just_locked = False
        max_failures = 0
        for key in keys:
            entry = _login_attempts.setdefault(key, {"failures": [], "locked_until": 0})
            # Prune failures outside the window
            entry["failures"] = [t for t in entry["failures"] if now - t < ATTEMPT_WINDOW_SECONDS]
            entry["failures"].append(now)
            count = len(entry["failures"])
            if count > max_failures:
                max_failures = count
            if count >= MAX_FAILED_ATTEMPTS:
                entry["locked_until"] = now + LOCKOUT_DURATION_SECONDS
                just_locked = True

        rem = LOCKOUT_DURATION_SECONDS if just_locked else 0
        return just_locked, rem, max_failures


# ── Tamper-Evident Audit Log (SHA-256 Hash Chain) ────────────────────────────

def _compute_record_hash(record_without_hash: dict) -> str:
    canonical = json.dumps(record_without_hash, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _get_last_hash_locked() -> str:
    global _last_hash_cache
    if _last_hash_cache is not None:
        return _last_hash_cache

    if not os.path.exists(AUDIT_LOG_FILE):
        _last_hash_cache = GENESIS_HASH
        return _last_hash_cache

    last_line = None
    try:
        with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    last_line = stripped
        if last_line:
            parsed = json.loads(last_line)
            _last_hash_cache = parsed.get("integrity_hash", GENESIS_HASH)
        else:
            _last_hash_cache = GENESIS_HASH
    except Exception:
        _last_hash_cache = GENESIS_HASH

    return _last_hash_cache


def log_audit_event(
    event_type: str,
    status: str = "SUCCESS",
    severity: str = "INFO",
    actor: dict = None,
    target: dict = None,
    details: dict = None
) -> dict:
    """
    Append an immutable, hash-chained audit record to data/audit.log.
    Never logs plaintext passwords.
    """
    global _last_hash_cache
    os.makedirs(DATA_DIR, exist_ok=True)

    # Resolve actor context from Flask request if not explicitly provided
    actor_info = dict(actor) if actor else {}
    if has_request_context():
        if "source_ip" not in actor_info:
            actor_info["source_ip"] = (
                request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or request.remote_addr
                or "unknown"
            )
        if "user_agent" not in actor_info:
            actor_info["user_agent"] = request.headers.get("User-Agent", "")[:160]
        if "username" not in actor_info:
            try:
                from core.auth import get_current_user
                u = get_current_user()
                if u:
                    actor_info.setdefault("user_id", u.get("id"))
                    actor_info.setdefault("username", u.get("username"))
                    actor_info.setdefault("role", u.get("role"))
            except Exception:
                pass

    actor_info.setdefault("username", "anonymous")
    actor_info.setdefault("role", "none")

    # Sanitize details so passwords and private keys are never written to audit log
    safe_details = {}
    if isinstance(details, dict):
        for k, v in details.items():
            if "password" in k.lower() or "private_key" in k.lower():
                safe_details[k] = "***REDACTED***"
            else:
                safe_details[k] = v

    target_info = dict(target) if isinstance(target, dict) else {}

    with _audit_lock:
        prev_hash = _get_last_hash_locked()
        record_core = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "severity": severity,
            "status": status,
            "actor": actor_info,
            "target": target_info,
            "details": safe_details,
            "prev_hash": prev_hash
        }
        integrity_hash = _compute_record_hash(record_core)
        record = dict(record_core)
        record["integrity_hash"] = integrity_hash

        try:
            with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            _last_hash_cache = integrity_hash
        except Exception as e:
            print(f"[AMIKO Audit] Failed to write audit log: {e}")

        return record


def verify_audit_chain() -> dict:
    """
    Verify the cryptographic SHA-256 hash chain across the entire audit.log file.
    Returns {"valid": bool, "total_entries": int, "corrupted_line": int | None}.
    """
    if not os.path.exists(AUDIT_LOG_FILE):
        return {"valid": True, "total_entries": 0, "corrupted_line": None}

    expected_prev = GENESIS_HASH
    count = 0

    with _audit_lock:
        try:
            with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    count += 1
                    record = json.loads(stripped)
                    stored_hash = record.get("integrity_hash")
                    prev_hash = record.get("prev_hash")

                    if prev_hash != expected_prev:
                        return {
                            "valid": False,
                            "total_entries": count,
                            "corrupted_line": line_no,
                            "reason": "Broken previous-hash link"
                        }

                    record_core = {k: v for k, v in record.items() if k != "integrity_hash"}
                    computed_hash = _compute_record_hash(record_core)
                    if computed_hash != stored_hash:
                        return {
                            "valid": False,
                            "total_entries": count,
                            "corrupted_line": line_no,
                            "reason": "Record hash mismatch (tampered content)"
                        }

                    expected_prev = stored_hash
        except Exception as e:
            return {
                "valid": False,
                "total_entries": count,
                "corrupted_line": count + 1,
                "reason": f"Parse error: {str(e)}"
            }

    return {"valid": True, "total_entries": count, "corrupted_line": None}


def get_audit_events(limit: int = 200, event_type: str = None, status: str = None) -> list[dict]:
    """Return the most recent audit events (newest first), optionally filtered."""
    if not os.path.exists(AUDIT_LOG_FILE):
        return []

    records = []
    with _audit_lock:
        try:
            with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        rec = json.loads(stripped)
                        if event_type and rec.get("event_type") != event_type:
                            continue
                        if status and rec.get("status") != status:
                            continue
                        records.append(rec)
                    except Exception:
                        continue
        except Exception:
            return []

    records.reverse()
    return records[:max(1, min(1000, int(limit or 200)))]
