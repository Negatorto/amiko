"""
AMIKO - Authentication & Role-Based Access Control (RBAC)
Implements the Principle of Least Privilege (NIS2 Art. 21.2.i):
  - admin:    Full control over users, groups, servers, settings, and all modules.
  - operator: Access only to assigned server groups; can use all operational modules
              (SSH terminal, Explorer read/write, Notes, Search) on assigned servers,
              but cannot modify global server/group/user configurations.
  - viewer:   Read-only access only to assigned server groups; can monitor vitals,
              browse/read files in Explorer, read Notes, and run searches,
              but cannot open SSH terminal or create/modify/delete files or notes.
Delegated permission:
  - can_manage_firewall: granted by an admin to individual operators; allows managing the
              Firewall & Fail2Ban configuration without global admin privileges.
"""

import os
import time
from urllib.parse import urlparse
from functools import wraps
from flask import session, request, redirect, url_for, jsonify, g
from werkzeug.exceptions import NotFound
from core.config import settings, ssh_sessions
from core.audit import log_audit_event
from core.client_ip import get_client_ip
from core.firewall import report_failure

SESSION_IDLE_TIMEOUT_SECONDS = int(os.environ.get("AMIKO_SESSION_TIMEOUT_MIN", "60")) * 60


def is_same_origin_request() -> bool:
    """
    Verify that if an Origin header is present, its host matches the request Host
    (or an explicitly whitelisted AMIKO_CORS_ORIGINS entry).
    Prevents Cross-Site Request Forgery (CSRF) and Cross-Site WebSocket Hijacking (CSWSH).
    """
    origin = request.headers.get("Origin")
    if not origin:
        return True
    extra = [o.strip() for o in os.environ.get("AMIKO_CORS_ORIGINS", "").split(",") if o.strip()]
    if origin in extra:
        return True
    try:
        parsed = urlparse(origin)
        return parsed.netloc == request.host
    except Exception:
        return False


ROLE_CAPABILITIES = {
    "admin": {
        "can_manage_settings": True,
        "can_manage_users": True,
        "can_manage_firewall": True,
        "can_write": True,
        "can_ssh": True,
        "blocked_modules": []
    },
    "operator": {
        "can_manage_settings": False,
        "can_manage_users": False,
        "can_manage_firewall": False,
        "can_write": True,
        "can_ssh": True,
        "blocked_modules": []
    },
    "viewer": {
        "can_manage_settings": False,
        "can_manage_users": False,
        "can_manage_firewall": False,
        "can_write": False,
        "can_ssh": False,
        "blocked_modules": ["ssh"]
    }
}

EXPLORER_WRITE_PATHS = {
    "/explorer/save-file",
    "/explorer/create-file",
    "/explorer/create-dir",
    "/explorer/delete-entry"
}


def get_current_user():
    """Return the currently authenticated user dict (enriched with capabilities) or None."""
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = settings.get_user_by_id(user_id, safe=True)
    if not user:
        session.clear()
        return None
    role = user.get("role", "viewer")
    capabilities = dict(ROLE_CAPABILITIES.get(role, ROLE_CAPABILITIES["viewer"]))
    if role == "operator" and user.get("can_manage_firewall"):
        capabilities["can_manage_firewall"] = True
    user["capabilities"] = capabilities
    return user


def filter_modules_for_user(modules, user):
    """Filter active modules according to the user's role capabilities and each module's permission manifest."""
    if not user:
        return []
    role = user.get("role", "viewer")
    blocked = set(user.get("capabilities", {}).get("blocked_modules", []))
    allowed = []
    for m in modules:
        if m["id"] in blocked:
            continue
        manifest_roles = (m.get("permissions") or {}).get("allowed_roles")
        if manifest_roles and role not in manifest_roles:
            continue
        allowed.append(m)
    return allowed


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            if request.path.startswith("/api/") or request.is_json:
                return jsonify({"error": "Authentication required", "status": "error", "success": False}), 401
            return redirect(url_for("login_page"))
        g.current_user = user
        return f(*args, **kwargs)
    return decorated


def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({"error": "Authentication required", "status": "error", "success": False}), 401
            if user.get("role") not in allowed_roles:
                return jsonify({
                    "error": f"Least Privilege: role '{user.get('role')}' is not authorized for this action.",
                    "status": "error",
                    "success": False
                }), 403
            g.current_user = user
            return f(*args, **kwargs)
        return decorated
    return decorator


def capability_required(capability):
    """Require a (role-derived or delegated) capability, e.g. 'can_manage_firewall'."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({"error": "Authentication required", "status": "error", "success": False}), 401
            if not user.get("capabilities", {}).get(capability):
                return jsonify({
                    "error": f"Least Privilege: missing permission '{capability}'.",
                    "status": "error",
                    "success": False
                }), 403
            g.current_user = user
            return f(*args, **kwargs)
        return decorated
    return decorator


def init_auth(app):
    """Register global request guards enforcing authentication and Least Privilege."""

    @app.before_request
    def enforce_least_privilege():
        path = request.path

        # 0. Same-Origin / Anti-CSRF verification on state-changing requests
        if request.method in ("POST", "PUT", "DELETE") and not is_same_origin_request():
            log_audit_event(
                event_type="CSRF_ORIGIN_BLOCKED",
                status="DENIED",
                severity="ALERT",
                target={"resource": path},
                details={"origin": request.headers.get("Origin", ""), "host": request.host}
            )
            report_failure("amiko-sec", "Cross-origin state-changing request (CSRF)")
            return jsonify({
                "error": "Cross-origin request blocked by security policy.",
                "status": "error",
                "success": False
            }), 403

        # 1. Public endpoints (login page, vault status/unseal, login API, 2FA challenge, static assets, read-only theme init)
        if (
            path in ("/login", "/api/vault/status", "/api/vault/unseal", "/favicon.svg")
            or path.startswith("/static/")
            or (path == "/api/themes" and request.method == "GET")
        ):
            return None

        # 1b. If the encrypted vault is sealed in RAM, block all other routes until unsealed
        if settings.is_sealed:
            session.clear()
            if path == "/":
                return redirect(url_for("login_page"))
            return jsonify({
                "error": "Vault is currently sealed. Please unseal the vault with the Master Passphrase.",
                "status": "error",
                "success": False,
                "vault_sealed": True
            }), 423

        if path in ("/api/auth/login", "/api/auth/2fa/verify"):
            return None

        # 2. Require authenticated user & enforce idle session timeout
        now_ts = time.time()
        last_act = session.get("last_activity")
        if session.get("user_id") and last_act and (now_ts - float(last_act) > SESSION_IDLE_TIMEOUT_SECONDS):
            expired_user = session.get("username", "unknown")
            session.clear()
            log_audit_event(
                event_type="AUTH_SESSION_EXPIRED",
                status="INFO",
                severity="NOTICE",
                actor={"username": expired_user, "source_ip": get_client_ip()},
                details={"idle_timeout_seconds": SESSION_IDLE_TIMEOUT_SECONDS}
            )

        user = get_current_user()
        if not user:
            if isinstance(request.routing_exception, NotFound):
                # Unauthenticated request to a non-existent endpoint: scanner/probing behaviour
                report_failure("amiko-sec", f"Unauthenticated probe of unknown path {path[:80]}")
            if path == "/":
                return redirect(url_for("login_page"))
            return jsonify({
                "error": "Authentication required. Please log in.",
                "status": "error",
                "success": False
            }), 401

        session["last_activity"] = now_ts
        g.current_user = user
        role = user.get("role", "viewer")

        # 3. Admin-only endpoints: User management, Vault Security, Audit Log, Backup & Server/Group mutations
        if path.startswith(("/api/users", "/api/vault", "/api/audit", "/api/backup", "/api/known-hosts", "/api/notifications")):
            if role != "admin":
                log_audit_event(
                    event_type="RBAC_ACCESS_DENIED",
                    status="DENIED",
                    severity="WARNING",
                    target={"resource": path},
                    details={"reason": "Admin role required", "method": request.method}
                )
                report_failure("amiko-sec", f"Non-admin access to reserved endpoint {path[:80]}")
                return jsonify({
                    "error": "Least Privilege: Only Administrators can access this resource.",
                    "status": "error",
                    "success": False
                }), 403

        if path.startswith(("/api/groups", "/api/servers")) and request.method in ("POST", "PUT", "DELETE"):
            if role != "admin":
                log_audit_event(
                    event_type="RBAC_ACCESS_DENIED",
                    status="DENIED",
                    severity="WARNING",
                    target={"resource": path},
                    details={"reason": "Admin role required for group/server mutation", "method": request.method}
                )
                report_failure("amiko-sec", f"Non-admin mutation attempt on {path[:80]}")
                return jsonify({
                    "error": "Least Privilege: Only Administrators can create, modify, or delete groups and servers.",
                    "status": "error",
                    "success": False
                }), 403

        # 3b. Firewall & Fail2Ban management: admin or delegated 'can_manage_firewall'
        if path.startswith("/api/firewall") and not user["capabilities"].get("can_manage_firewall"):
            log_audit_event(
                event_type="RBAC_ACCESS_DENIED",
                status="DENIED",
                severity="WARNING",
                target={"resource": path},
                details={"reason": "can_manage_firewall permission required", "method": request.method}
            )
            report_failure("amiko-sec", f"Unauthorized firewall management attempt on {path[:80]}")
            return jsonify({
                "error": "Least Privilege: Firewall management requires the 'can_manage_firewall' permission.",
                "status": "error",
                "success": False
            }), 403

        # 4. Viewer Read-Only enforcement (core routes + module permission manifests)
        if role == "viewer":
            from core.plugins import get_write_endpoints_map
            manifest_write_map = get_write_endpoints_map()

            if path == "/ssh-command":
                log_audit_event(
                    event_type="RBAC_ACCESS_DENIED",
                    status="DENIED",
                    severity="WARNING",
                    target={"resource": path},
                    details={"reason": "Viewer role cannot execute SSH commands"}
                )
                return jsonify({
                    "error": "Least Privilege: Viewer role cannot execute SSH commands.",
                    "status": "error",
                    "success": False
                }), 403

            if path in EXPLORER_WRITE_PATHS or path in manifest_write_map:
                mod_label = manifest_write_map.get(path, "Explorer")
                log_audit_event(
                    event_type="RBAC_ACCESS_DENIED",
                    status="DENIED",
                    severity="WARNING",
                    target={"resource": path},
                    details={"reason": f"Viewer role has read-only access in {mod_label}"}
                )
                return jsonify({
                    "error": f"Least Privilege: Viewer role has read-only access in {mod_label}.",
                    "message": f"Least Privilege: Viewer role has read-only access in {mod_label}.",
                    "status": "error",
                    "success": False
                }), 403

            if path.startswith("/notes") and request.method in ("POST", "PUT", "DELETE"):
                log_audit_event(
                    event_type="RBAC_ACCESS_DENIED",
                    status="DENIED",
                    severity="WARNING",
                    target={"resource": path},
                    details={"reason": "Viewer role has read-only access in Notes"}
                )
                return jsonify({
                    "error": "Least Privilege: Viewer role has read-only access in Notes.",
                    "status": "error",
                    "success": False
                }), 403

        # 5. Server Group Access Enforcement for non-admin roles (operator & viewer)
        if role != "admin":
            # JSON POST endpoints carrying target server info
            if path in ("/ssh-connect", "/ssh-command") or path.startswith(("/explorer/", "/services/")):
                data = request.get_json(silent=True) or {}
                server_id = data.get("server_id")
                hostname = data.get("hostname")
                port = data.get("port", 22)
                session_id = data.get("session_id")

                if not hostname and not server_id and session_id and session_id in ssh_sessions:
                    hostname = ssh_sessions[session_id].get("hostname")
                    port = ssh_sessions[session_id].get("port", 22)
                    server_id = ssh_sessions[session_id].get("server_id")

                if (hostname or server_id) and not settings.can_user_access_server(
                    user, hostname=hostname, port=port, server_id=server_id
                ):
                    target_label = hostname or server_id
                    log_audit_event(
                        event_type="RBAC_ACCESS_DENIED",
                        status="DENIED",
                        severity="WARNING",
                        target={"server_id": server_id, "hostname": hostname, "port": port, "resource": path},
                        details={"reason": "Server not in user allowed_groups"}
                    )
                    return jsonify({
                        "error": f"Least Privilege: Access to server '{target_label}' is not permitted for your assigned groups.",
                        "message": f"Least Privilege: Access to server '{target_label}' is not permitted for your assigned groups.",
                        "status": "error",
                        "success": False
                    }), 403

            # SSE / GET endpoints carrying session_id or hostname
            if path in ("/ssh-stats", "/search", "/search-files", "/search-large-files"):
                session_id = request.args.get("session_id")
                if session_id and session_id in ssh_sessions:
                    s_data = ssh_sessions[session_id]
                    if not settings.can_user_access_server(user, hostname=s_data.get("hostname"), port=s_data.get("port", 22)):
                        return "Least Privilege: Forbidden server access", 403

            if path == "/notes/list":
                hostname = request.args.get("hostname", "").strip()
                group_id = request.args.get("group_id", "").strip()
                server_id = request.args.get("server_id", "").strip()
                if hostname and not settings.can_user_access_server(
                    user, hostname=hostname, server_id=server_id, group_id=group_id
                ):
                    return jsonify([])

        return None
