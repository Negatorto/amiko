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
"""

from functools import wraps
from flask import session, request, redirect, url_for, jsonify, g
from core.config import settings, ssh_sessions


ROLE_CAPABILITIES = {
    "admin": {
        "can_manage_settings": True,
        "can_manage_users": True,
        "can_write": True,
        "can_ssh": True,
        "blocked_modules": []
    },
    "operator": {
        "can_manage_settings": False,
        "can_manage_users": False,
        "can_write": True,
        "can_ssh": True,
        "blocked_modules": []
    },
    "viewer": {
        "can_manage_settings": False,
        "can_manage_users": False,
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
    user["capabilities"] = ROLE_CAPABILITIES.get(role, ROLE_CAPABILITIES["viewer"])
    return user


def filter_modules_for_user(modules, user):
    """Filter active modules according to the user's role capabilities."""
    if not user:
        return []
    blocked = set(user.get("capabilities", {}).get("blocked_modules", []))
    return [m for m in modules if m["id"] not in blocked]


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


def init_auth(app):
    """Register global request guards enforcing authentication and Least Privilege."""

    @app.before_request
    def enforce_least_privilege():
        path = request.path

        # 1. Public endpoints (login page, login API, static assets, read-only theme init)
        if (
            path in ("/login", "/api/auth/login", "/favicon.svg")
            or path.startswith("/static/")
            or (path == "/api/themes" and request.method == "GET")
        ):
            return None

        # 2. Require authenticated user
        user = get_current_user()
        if not user:
            if path == "/":
                return redirect(url_for("login_page"))
            return jsonify({
                "error": "Authentication required. Please log in.",
                "status": "error",
                "success": False
            }), 401

        g.current_user = user
        role = user.get("role", "viewer")

        # 3. Admin-only endpoints: User management & Server/Group mutations
        if path.startswith("/api/users"):
            if role != "admin":
                return jsonify({
                    "error": "Least Privilege: Only Administrators can manage users.",
                    "status": "error",
                    "success": False
                }), 403

        if path.startswith(("/api/groups", "/api/servers")) and request.method in ("POST", "PUT", "DELETE"):
            if role != "admin":
                return jsonify({
                    "error": "Least Privilege: Only Administrators can create, modify, or delete groups and servers.",
                    "status": "error",
                    "success": False
                }), 403

        # 4. Viewer Read-Only enforcement
        if role == "viewer":
            if path == "/ssh-command":
                return jsonify({
                    "error": "Least Privilege: Viewer role cannot execute SSH commands.",
                    "status": "error",
                    "success": False
                }), 403

            if path in EXPLORER_WRITE_PATHS:
                return jsonify({
                    "error": "Least Privilege: Viewer role has read-only access in Explorer.",
                    "message": "Least Privilege: Viewer role has read-only access in Explorer.",
                    "status": "error",
                    "success": False
                }), 403

            if path.startswith("/notes") and request.method in ("POST", "PUT", "DELETE"):
                return jsonify({
                    "error": "Least Privilege: Viewer role has read-only access in Notes.",
                    "status": "error",
                    "success": False
                }), 403

        # 5. Server Group Access Enforcement for non-admin roles (operator & viewer)
        if role != "admin":
            # JSON POST endpoints carrying target server info
            if path in ("/ssh-connect", "/ssh-command") or path.startswith("/explorer/"):
                data = request.get_json(silent=True) or {}
                hostname = data.get("hostname")
                port = data.get("port", 22)
                session_id = data.get("session_id")

                if not hostname and session_id and session_id in ssh_sessions:
                    hostname = ssh_sessions[session_id].get("hostname")
                    port = ssh_sessions[session_id].get("port", 22)

                if hostname and not settings.can_user_access_server(user, hostname=hostname, port=port):
                    return jsonify({
                        "error": f"Least Privilege: Access to server '{hostname}' is not permitted for your assigned groups.",
                        "message": f"Least Privilege: Access to server '{hostname}' is not permitted for your assigned groups.",
                        "status": "error",
                        "success": False
                    }), 403

            # SSE / GET endpoints carrying session_id or hostname
            if path in ("/ssh-stats", "/search", "/search-large-files"):
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
