"""
AMIKO - Plugin Loader, Permission Manifests & Integrity Verification
Validates module permission manifests (`permissions` in `module.json`) and verifies
SHA-256 cryptographic digests (`integrity` in `module.json`) of all plugin files
(`template.html`, `client.js`, `backend.py`) before loading or executing them.
"""

import os
import json
import hashlib
import importlib.util
import re
from markupsafe import Markup

from core.config import MODULES_DIR
from core.audit import log_audit_event

VALID_ROLES = ("admin", "operator", "viewer")
VALID_CAPABILITIES = (
    "ssh_shell",
    "ssh_exec",
    "sftp_read",
    "sftp_write",
    "local_vault",
)
TRACKED_MODULE_FILES = ("template.html", "client.js", "backend.py")
ALLOWED_DIR_ENTRIES = {"module.json", "template.html", "client.js", "backend.py", "__pycache__"}
FORBIDDEN_EXTRA_EXTENSIONS = (".py", ".pyc", ".pyo", ".js", ".mjs", ".sh", ".so", ".dll", ".html")

# Runtime registries populated during load_modules()
_VERIFIED_MODULES: dict[str, dict] = {}
_WRITE_ENDPOINTS_MAP: dict[str, str] = {}  # endpoint_path -> module_id


def get_integrity_mode() -> str:
    """
    Return 'enforce' (default) or 'warn' based on AMIKO_PLUGIN_INTEGRITY env var.
    In 'enforce' mode, modules with missing or mismatched SHA-256 digests are blocked.
    """
    mode = os.environ.get("AMIKO_PLUGIN_INTEGRITY", "enforce").strip().lower()
    return "warn" if mode == "warn" else "enforce"


def compute_file_sha256(filepath: str) -> str:
    """Return the lowercase hexadecimal SHA-256 digest of a file on disk."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_module_hashes(mod_path: str) -> dict[str, str]:
    """Compute SHA-256 hashes for all present tracked files in a module folder."""
    hashes = {}
    for fname in TRACKED_MODULE_FILES:
        fpath = os.path.join(mod_path, fname)
        if os.path.isfile(fpath):
            hashes[fname] = compute_file_sha256(fpath)
    return hashes


def normalize_permissions(raw_perms: dict | None, mod_name: str = "") -> tuple[dict, list[str]]:
    """
    Validate and normalize the `permissions` block of a module's `module.json`.
    Returns (normalized_dict, list_of_warnings_or_errors).
    """
    issues = []
    if not isinstance(raw_perms, dict):
        issues.append("Missing or invalid 'permissions' manifest object in module.json")
        raw_perms = {}

    # 1. allowed_roles
    raw_roles = raw_perms.get("allowed_roles")
    if not isinstance(raw_roles, list) or not raw_roles:
        allowed_roles = list(VALID_ROLES)
        issues.append("permissions.allowed_roles missing or empty; defaulting to all roles")
    else:
        allowed_roles = []
        for r in raw_roles:
            if r in VALID_ROLES:
                if r not in allowed_roles:
                    allowed_roles.append(r)
            else:
                issues.append(f"Unknown role '{r}' in permissions.allowed_roles")
        if not allowed_roles:
            allowed_roles = ["admin"]
            issues.append("No valid roles in permissions.allowed_roles; restricting to ['admin']")

    # 2. capabilities
    raw_caps = raw_perms.get("capabilities", [])
    capabilities = []
    if not isinstance(raw_caps, list):
        issues.append("permissions.capabilities must be a list")
    else:
        for c in raw_caps:
            if c in VALID_CAPABILITIES:
                if c not in capabilities:
                    capabilities.append(c)
            else:
                issues.append(f"Unknown capability '{c}' in permissions.capabilities")

    # 3. requires_connection
    requires_connection = bool(raw_perms.get("requires_connection", True))

    # 4. write_endpoints
    raw_write = raw_perms.get("write_endpoints", [])
    write_endpoints = []
    if not isinstance(raw_write, list):
        issues.append("permissions.write_endpoints must be a list of route paths")
    else:
        for ep in raw_write:
            if isinstance(ep, str) and ep.startswith("/"):
                write_endpoints.append(ep.strip())
            else:
                issues.append(f"Invalid write_endpoint '{ep}' (must start with '/')")

    normalized = {
        "allowed_roles": allowed_roles,
        "capabilities": capabilities,
        "requires_connection": requires_connection,
        "write_endpoints": write_endpoints,
    }
    return normalized, issues


def verify_module_integrity(mod_name: str, mod_path: str, mod_data: dict) -> tuple[bool, list[str], dict[str, str]]:
    """
    Verify a module's file integrity against its `module.json["integrity"]` block.
    Also checks for unexpected executable/script files inside the module directory.

    Returns:
        (is_valid: bool, errors: list[str], actual_hashes: dict[str, str])
    """
    errors = []
    actual_hashes = compute_module_hashes(mod_path)

    # 1. Check required base files exist
    for req_file in ("template.html", "client.js"):
        if req_file not in actual_hashes:
            errors.append(f"Required module file '{req_file}' is missing")

    # 2. Check for unexpected executable/code files in the module directory
    try:
        for entry in os.listdir(mod_path):
            if entry in ALLOWED_DIR_ENTRIES or entry.startswith("."):
                continue
            full_entry = os.path.join(mod_path, entry)
            if os.path.isdir(full_entry):
                errors.append(f"Unexpected subdirectory '{entry}/' in module folder")
            elif entry.lower().endswith(FORBIDDEN_EXTRA_EXTENSIONS):
                errors.append(f"Untracked code file '{entry}' found in module folder")
    except OSError as e:
        errors.append(f"Cannot scan module directory: {e}")

    # 3. Validate 'integrity' block in module.json
    integrity = mod_data.get("integrity")
    if not isinstance(integrity, dict):
        errors.append("Missing 'integrity' block in module.json (run: python3 utils/verify_modules.py --sign)")
        return False, errors, actual_hashes

    algo = str(integrity.get("algorithm", "sha256")).lower()
    if algo != "sha256":
        errors.append(f"Unsupported integrity algorithm '{algo}' (expected 'sha256')")

    manifest_files = integrity.get("files")
    if not isinstance(manifest_files, dict) or not manifest_files:
        errors.append("Missing or empty 'integrity.files' map in module.json")
        return False, errors, actual_hashes

    # 4. Check every present tracked file has a matching SHA-256 digest
    for fname, actual_hex in actual_hashes.items():
        expected_raw = manifest_files.get(fname)
        if not expected_raw or not isinstance(expected_raw, str):
            errors.append(f"File '{fname}' is present on disk but not listed in integrity.files")
            continue
        expected_hex = expected_raw.removeprefix("sha256:").strip().lower()
        if actual_hex.lower() != expected_hex:
            errors.append(
                f"SHA-256 mismatch for '{fname}' (expected {expected_hex[:12]}..., got {actual_hex[:12]}...)"
            )

    # 5. Check no file listed in integrity.files was deleted from disk
    for fname in manifest_files.keys():
        if fname not in actual_hashes:
            errors.append(f"File '{fname}' is listed in integrity.files but missing from disk")

    return len(errors) == 0, errors, actual_hashes


def verify_static_module_file(mod_name: str, filename: str) -> tuple[bool, str]:
    """
    Verify on-the-fly that a requested static module asset (e.g. `client.js`) belongs to
    a loaded, verified module and that its SHA-256 on disk still matches the manifest.
    """
    if filename != "client.js":
        return False, "Only client.js may be served from module static route"

    manifest = _VERIFIED_MODULES.get(mod_name)
    if not manifest:
        return False, f"Module '{mod_name}' is not loaded or failed integrity verification"

    expected_hashes = manifest.get("hashes", {})
    expected_hex = expected_hashes.get(filename)
    fpath = os.path.join(MODULES_DIR, mod_name, filename)
    if not os.path.isfile(fpath):
        return False, f"File '{filename}' not found"

    if expected_hex:
        actual_hex = compute_file_sha256(fpath)
        if actual_hex.lower() != expected_hex.lower():
            log_audit_event(
                event_type="PLUGIN_INTEGRITY_FAILED",
                status="DENIED",
                severity="CRITICAL",
                target={"module": mod_name, "file": filename},
                details={
                    "phase": "runtime_static_serve",
                    "expected_sha256": expected_hex,
                    "actual_sha256": actual_hex,
                },
            )
            if get_integrity_mode() == "enforce":
                return False, f"Runtime integrity check failed for {mod_name}/{filename}"

    return True, "ok"


def get_module_manifest(mod_name: str) -> dict | None:
    """Return the verified manifest metadata for a loaded module, or None."""
    return _VERIFIED_MODULES.get(mod_name)


def get_write_endpoints_map() -> dict[str, str]:
    """Return a dict mapping {endpoint_path: module_name} for all loaded modules' write endpoints."""
    return dict(_WRITE_ENDPOINTS_MAP)


def inspect_all_modules() -> list[dict]:
    """
    Inspect all modules on disk (both enabled and disabled) and return their permission
    manifest and live integrity verification status. Used by CLI and admin diagnostics.
    """
    results = []
    if not os.path.exists(MODULES_DIR):
        return results

    for mod_name in sorted(os.listdir(MODULES_DIR)):
        mod_path = os.path.join(MODULES_DIR, mod_name)
        if not os.path.isdir(mod_path) or mod_name.startswith("."):
            continue
        json_path = os.path.join(mod_path, "module.json")
        if not os.path.exists(json_path):
            continue

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                mod_data = json.load(f)
        except Exception as e:
            results.append({
                "id": mod_name,
                "name": mod_name,
                "enabled": False,
                "integrity_ok": False,
                "permissions_ok": False,
                "issues": [f"Invalid module.json: {e}"],
                "permissions": {},
                "hashes": {},
            })
            continue

        perms, perm_issues = normalize_permissions(mod_data.get("permissions"), mod_name)
        int_ok, int_errors, actual_hashes = verify_module_integrity(mod_name, mod_path, mod_data)

        results.append({
            "id": mod_name,
            "name": mod_data.get("name", mod_name),
            "enabled": bool(mod_data.get("enabled", True)),
            "index": mod_data.get("index", 999),
            "integrity_ok": int_ok,
            "permissions_ok": len(perm_issues) == 0,
            "issues": perm_issues + int_errors,
            "permissions": perms,
            "hashes": actual_hashes,
        })

    results.sort(key=lambda x: (x.get("index", 999), x["id"]))
    return results


def sign_module_manifest(mod_name: str) -> tuple[bool, str, dict]:
    """
    Compute SHA-256 digests for `template.html`, `client.js`, and `backend.py` (if present),
    ensure a valid `permissions` object exists, and write the updated `module.json` to disk.
    """
    mod_path = os.path.join(MODULES_DIR, mod_name)
    json_path = os.path.join(mod_path, "module.json")
    if not os.path.isfile(json_path):
        return False, f"module.json not found for '{mod_name}'", {}

    with open(json_path, "r", encoding="utf-8") as f:
        mod_data = json.load(f)

    perms, _ = normalize_permissions(mod_data.get("permissions"), mod_name)
    mod_data["permissions"] = perms

    hashes = compute_module_hashes(mod_path)
    mod_data["integrity"] = {
        "algorithm": "sha256",
        "files": hashes,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(mod_data, f, indent=4, ensure_ascii=False)
        f.write("\n")

    return True, f"Signed {len(hashes)} files for module '{mod_name}'", hashes


def load_modules(app):
    """Discover, verify integrity & permission manifests, and load enabled AMIKO modules."""
    loaded_modules = []
    _VERIFIED_MODULES.clear()
    _WRITE_ENDPOINTS_MAP.clear()

    if not os.path.exists(MODULES_DIR):
        os.makedirs(MODULES_DIR)
        return loaded_modules

    mode = get_integrity_mode()

    for mod_name in sorted(os.listdir(MODULES_DIR)):
        mod_path = os.path.join(MODULES_DIR, mod_name)
        if not os.path.isdir(mod_path) or mod_name.startswith("."):
            continue

        json_path = os.path.join(mod_path, "module.json")
        if not os.path.exists(json_path):
            continue

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                mod_data = json.load(f)

            if not mod_data.get("enabled", True):
                continue

            # 1. Validate & normalize permission manifest
            permissions, perm_issues = normalize_permissions(mod_data.get("permissions"), mod_name)
            for p_issue in perm_issues:
                print(f"[AMIKO Plugin Security] Manifest notice for '{mod_name}': {p_issue}")

            # 2. Verify cryptographic SHA-256 integrity of module files BEFORE executing anything
            int_ok, int_errors, actual_hashes = verify_module_integrity(mod_name, mod_path, mod_data)
            if not int_ok:
                print(f"[AMIKO Plugin Security] INTEGRITY CHECK FAILED for '{mod_name}': {'; '.join(int_errors)}")
                log_audit_event(
                    event_type="PLUGIN_INTEGRITY_FAILED",
                    status="DENIED" if mode == "enforce" else "WARNING",
                    severity="CRITICAL",
                    actor={"username": "system", "role": "system", "source_ip": "127.0.0.1"},
                    target={"module": mod_name},
                    details={"mode": mode, "errors": int_errors},
                )
                if mode == "enforce":
                    print(f"[AMIKO Plugin Security] Blocked loading of unverified module: {mod_name}")
                    continue

            # 3. Process SVG icon
            if "icon" in mod_data and mod_data["icon"].startswith("<svg"):
                svg_str = mod_data["icon"]
                match_vb = re.search(r'viewBox=[\'"]([^\'"]+)[\'"]', svg_str)
                viewbox = match_vb.group(1) if match_vb else "0 0 24 24"

                match_inner = re.search(r"<svg[^>]*>(.*?)</svg>", svg_str, re.IGNORECASE | re.DOTALL)
                inner_html = match_inner.group(1) if match_inner else ""

                mod_data["symbol"] = Markup(f'<symbol id="icon-{mod_name}" viewBox="{viewbox}">{inner_html}</symbol>')
                mod_data["icon"] = Markup(svg_str)
            else:
                mod_data["symbol"] = ""

            # 4. Load verified template.html
            template_html = ""
            tpl_path = os.path.join(mod_path, "template.html")
            if os.path.exists(tpl_path):
                with open(tpl_path, "r", encoding="utf-8") as f:
                    template_html = f.read()

            mod_data["template_html"] = template_html
            mod_data["id"] = mod_name
            mod_data["permissions"] = permissions
            mod_data["integrity_verified"] = int_ok

            # 5. Load and register verified backend.py blueprint (if present)
            backend_path = os.path.join(mod_path, "backend.py")
            if os.path.exists(backend_path):
                spec = importlib.util.spec_from_file_location(f"module_{mod_name}", backend_path)
                mod_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod_module)

                if hasattr(mod_module, "blueprint"):
                    app.register_blueprint(mod_module.blueprint)
                    print(f"[AMIKO Plugin System] Registered verified Backend Blueprint for: {mod_name}")

            # 6. Record verified manifest and register write endpoints for RBAC enforcement
            _VERIFIED_MODULES[mod_name] = {
                "id": mod_name,
                "name": mod_data.get("name", mod_name),
                "permissions": permissions,
                "hashes": actual_hashes,
                "integrity_verified": int_ok,
            }
            for ep in permissions.get("write_endpoints", []):
                _WRITE_ENDPOINTS_MAP[ep] = mod_name

            loaded_modules.append(mod_data)
            print(
                f"[AMIKO Plugin System] Loaded verified module '{mod_name}' "
                f"(roles={permissions['allowed_roles']}, caps={permissions['capabilities']})"
            )

        except Exception as e:
            print(f"[AMIKO Plugin System] Error loading module {mod_name}: {e}")

    loaded_modules.sort(key=lambda x: x.get("index", 999))
    return loaded_modules
