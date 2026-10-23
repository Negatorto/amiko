"""
AMIKO - Explorer Module Backend
Provides directory listing and text file reading via SFTP/FTP.

OPTIMIZATION: SSH Connection Pool
  Each request no longer opens a new SSH handshake.
  Connections are cached per (host, port, user) for up to POOL_TTL seconds.
  A new SFTP channel is opened on the existing transport (cheap).
"""

from flask import Blueprint, request, jsonify
import stat
import os
import time
import hashlib
import threading
from io import BytesIO
from utils.sftp_helper import connect_sftp, connect_ftp
from core.config import settings as _amiko_settings, ssh_sessions as _amiko_ssh_sessions
from core.auth import get_current_user
from core.audit import log_audit_event

blueprint = Blueprint('explorer', __name__)

# Fallback hard-limit (used only when settings cannot be read)
_HARD_MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB absolute ceiling

def _get_max_file_size() -> int:
    """Return the user-configured max file size in bytes (1–20 MB)."""
    try:
        mb = int(_amiko_settings.get_ui_prefs().get('explorer_max_file_mb', 10))
        mb = max(1, min(20, mb))
    except Exception:
        mb = 10
    return mb * 1024 * 1024

def _get_show_hidden() -> bool:
    """Return True if hidden files (dotfiles) should be listed."""
    try:
        return bool(_amiko_settings.get_ui_prefs().get('explorer_show_hidden', False))
    except Exception:
        return False

POOL_TTL = 300  # seconds — keep pooled SSH + SFTP channel warm for 5 minutes

# ─── SSH + Persistent SFTP Channel Pool ────────────────────────────────────
# Structure: { pool_key → { 'ssh': SSHClient, 'sftp': SFTPClient, 'sftp_busy': bool, 'owns_ssh': bool, 'last_used': float } }
_ssh_pool: dict = {}
_pool_lock = threading.Lock()


def _pool_key(hostname: str, port: int, username: str, password: str) -> str:
    """Stable cache key — hashes the password so it's not stored in plain text."""
    pw_hash = hashlib.sha256(password.encode()).hexdigest()[:16]
    return f"{hostname}:{port}:{username}:{pw_hash}"


def _is_sftp_alive(sftp) -> bool:
    try:
        return sftp is not None and sftp.sock is not None and not sftp.sock.closed
    except Exception:
        return False


def _get_sftp(hostname: str, port: int, username: str, password: str):
    """
    Return (ssh_client, sftp_client, is_persistent_sftp).
    Reuses BOTH the existing SSH transport AND the persistent SFTP subsystem channel
    to eliminate per-request channel/process-spawn round-trips.
    """
    key = _pool_key(hostname, port, username, password)

    with _pool_lock:
        entry = _ssh_pool.get(key)
        if entry:
            try:
                transport = entry['ssh'].get_transport()
                if transport and transport.is_active():
                    entry['last_used'] = time.time()
                    # Fast path: reuse persistent SFTP channel if not currently in use
                    if not entry.get('sftp_busy') and _is_sftp_alive(entry.get('sftp')):
                        entry['sftp_busy'] = True
                        return entry['ssh'], entry['sftp'], key
                    # If persistent channel was closed, reopen and store it
                    if not entry.get('sftp_busy'):
                        sftp = entry['ssh'].open_sftp()
                        entry['sftp'] = sftp
                        entry['sftp_busy'] = True
                        return entry['ssh'], sftp, key
                    # Concurrent request while persistent SFTP is busy: open ephemeral SFTP channel
                    return entry['ssh'], entry['ssh'].open_sftp(), None
            except Exception:
                pass
            # Dead connection — remove and fall through to reconnect
            try:
                if entry.get('sftp'):
                    entry['sftp'].close()
            except Exception:
                pass
            if entry.get('owns_ssh', True):
                try:
                    entry['ssh'].close()
                except Exception:
                    pass
            del _ssh_pool[key]

    # Try to reuse the active SSH transport from the global session before opening a new TCP/SSH connection
    reused_ssh = None
    for s_data in list(_amiko_ssh_sessions.values()):
        if (
            s_data.get('hostname') == hostname
            and int(s_data.get('port', 22)) == int(port)
            and s_data.get('username') == username
        ):
            cand_ssh = s_data.get('ssh')
            if cand_ssh:
                t = cand_ssh.get_transport()
                if t and t.is_active():
                    reused_ssh = cand_ssh
                    break

    if reused_ssh is not None:
        ssh = reused_ssh
        t = ssh.get_transport()
        if t:
            t.set_keepalive(30)
        sftp = ssh.open_sftp()
        owns_ssh = False
    else:
        ssh, sftp = connect_sftp(hostname, port, username, password)
        t = ssh.get_transport()
        if t:
            t.set_keepalive(30)
        owns_ssh = True

    with _pool_lock:
        _ssh_pool[key] = {
            'ssh': ssh,
            'sftp': sftp,
            'sftp_busy': True,
            'owns_ssh': owns_ssh,
            'last_used': time.time()
        }
        _cleanup_pool_locked()

    return ssh, sftp, key


def _cleanup_pool_locked():
    """Remove stale entries. Must be called while _pool_lock is held."""
    now = time.time()
    dead = [k for k, v in _ssh_pool.items() if not v.get('sftp_busy') and now - v['last_used'] > POOL_TTL]
    for k in dead:
        entry = _ssh_pool.pop(k, None)
        if entry:
            try:
                if entry.get('sftp'):
                    entry['sftp'].close()
            except Exception:
                pass
            if entry.get('owns_ssh', True):
                try:
                    entry['ssh'].close()
                except Exception:
                    pass


def _release_sftp(sftp, persistent_key):
    """
    If sftp is the pooled persistent SFTP channel, keep it open for instant reuse;
    only close ephemeral overflow channels.
    """
    if persistent_key:
        with _pool_lock:
            entry = _ssh_pool.get(persistent_key)
            if entry and entry.get('sftp') is sftp:
                entry['sftp_busy'] = False
                entry['last_used'] = time.time()
                return
    try:
        if sftp:
            sftp.close()
    except Exception:
        pass


def _invalidate_pool_entry(hostname: str, port: int, username: str, password: str):
    """Remove a broken connection from the pool."""
    key = _pool_key(hostname, port, username, password)
    with _pool_lock:
        entry = _ssh_pool.pop(key, None)
        if entry:
            try:
                if entry.get('sftp'):
                    entry['sftp'].close()
            except Exception:
                pass
            if entry.get('owns_ssh', True):
                try:
                    entry['ssh'].close()
                except Exception:
                    pass


# ─── Helpers ──────────────────────────────────────────────────────────────

def _get_connection_params():
    """Extract and resolve connection parameters from request/session/vault."""
    data = request.get_json(silent=True) or {}
    resolved = _amiko_settings.resolve_connection_credentials(
        data,
        user=get_current_user(),
        ssh_sessions=_amiko_ssh_sessions
    )
    if not resolved:
        return None
    secret = resolved.get('private_key') or resolved.get('password', '')
    return resolved['hostname'], resolved['port'], resolved['username'], secret, data


def _format_size(size_bytes):
    """Format byte size to human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


# ─── Routes ───────────────────────────────────────────────────────────────

@blueprint.route('/explorer/list-dir', methods=['POST'])
def list_dir():
    """List the contents of a remote directory."""
    params = _get_connection_params()
    if not params:
        return jsonify({"status": "error", "message": "Missing connection parameters."}), 400

    hostname, port, username, password, data = params
    path = data.get('path', '/') or '/'
    path = path.strip() or '/'

    ftp_client = None

    try:
        entries = []

        if port == 21:
            # ── FTP mode (no pool — ftplib connections are not thread-safe / reusable) ──
            ftp_client = connect_ftp(hostname, port, username, password)
            try:
                ftp_client.cwd(path)
            except Exception:
                return jsonify({"status": "error", "message": f"Cannot access directory: {path}"}), 400

            try:
                for name, facts in ftp_client.mlsd(path):
                    if name in ('.', '..'):
                        continue
                    is_dir = facts.get('type', '') == 'dir'
                    size = int(facts.get('size', 0)) if not is_dir else 0
                    entries.append({
                        "name": name,
                        "is_dir": is_dir,
                        "size": size,
                        "size_display": _format_size(size) if not is_dir else "—",
                        "modified": facts.get('modify', '')
                    })
            except Exception:
                # Fallback: NLST (no per-file cwd loop — just mark all as files)
                names = ftp_client.nlst(path)
                for full_path_entry in names:
                    name = os.path.basename(full_path_entry)
                    if name in ('.', '..'):
                        continue
                    if name.startswith('.') and not _get_show_hidden():
                        continue
                    entries.append({
                        "name": name,
                        "is_dir": False,
                        "size": 0,
                        "size_display": "—",
                        "modified": ""
                    })
        else:
            # ── SFTP mode — reuse pooled SSH transport ──
            ssh, sftp, from_pool = _get_sftp(hostname, port, username, password)
            try:
                try:
                    file_list = sftp.listdir_attr(path)
                except IOError:
                    return jsonify({"status": "error", "message": f"Cannot access directory: {path}"}), 400

                show_hidden = _get_show_hidden()
                for attr in file_list:
                    name = attr.filename
                    if name in ('.', '..'):
                        continue
                    if name.startswith('.') and not show_hidden:
                        continue
                    is_dir = stat.S_ISDIR(attr.st_mode) if attr.st_mode else False
                    size = attr.st_size or 0
                    entries.append({
                        "name": name,
                        "is_dir": is_dir,
                        "size": size,
                        "size_display": _format_size(size) if not is_dir else "—",
                        "modified": attr.st_mtime or 0
                    })
            except Exception as e:
                _invalidate_pool_entry(hostname, port, username, password)
                raise
            finally:
                _release_sftp(sftp, from_pool)

        entries.sort(key=lambda e: (not e['is_dir'], e['name'].lower()))
        return jsonify({"status": "ok", "path": path, "entries": entries})

    except ConnectionRefusedError:
        return jsonify({"status": "error", "message": "Connection refused. Check host and port."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error: {str(e)}"}), 500
    finally:
        if ftp_client:
            try:
                ftp_client.quit()
            except Exception:
                pass


@blueprint.route('/explorer/read-file', methods=['POST'])
def read_file():
    """Read the text content of a remote file."""
    params = _get_connection_params()
    if not params:
        return jsonify({"status": "error", "message": "Missing connection parameters."}), 400

    hostname, port, username, password, data = params
    path = data.get('path', '')
    if not path:
        return jsonify({"status": "error", "message": "No file path specified."}), 400

    ftp_client = None

    try:
        filename = os.path.basename(path)
        file_size = 0

        if port == 21:
            ftp_client = connect_ftp(hostname, port, username, password)
            try:
                file_size = ftp_client.size(path) or 0
            except Exception:
                file_size = 0

            if file_size > _get_max_file_size():
                return jsonify({"status": "error", "message": f"File too large ({_format_size(file_size)}). Max: {_format_size(_get_max_file_size())}."}), 400

            buffer = BytesIO()
            ftp_client.retrbinary(f'RETR {path}', buffer.write)
            raw = buffer.getvalue()
        else:
            ssh, sftp, from_pool = _get_sftp(hostname, port, username, password)
            try:
                try:
                    file_stat = sftp.stat(path)
                    file_size = file_stat.st_size or 0
                except IOError:
                    return jsonify({"status": "error", "message": f"File not found: {path}"}), 404

                if file_size > _get_max_file_size():
                    return jsonify({"status": "error", "message": f"File too large ({_format_size(file_size)}). Max: {_format_size(_get_max_file_size())}."}), 400

                with sftp.open(path, 'rb') as f:
                    if file_size > 0:
                        f.prefetch(file_size)
                    raw = f.read()
            except Exception:
                _invalidate_pool_entry(hostname, port, username, password)
                raise
            finally:
                _release_sftp(sftp, from_pool)

        # Decode
        try:
            content = raw.decode('utf-8')
        except (UnicodeDecodeError, AttributeError):
            try:
                content = raw.decode('latin-1')
            except Exception:
                return jsonify({"status": "error", "message": "Cannot decode file: it appears to be a binary file."}), 400

        return jsonify({
            "status": "ok",
            "filename": filename,
            "path": path,
            "content": content,
            "size": file_size,
            "size_display": _format_size(file_size)
        })

    except ConnectionRefusedError:
        return jsonify({"status": "error", "message": "Connection refused. Check host and port."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error: {str(e)}"}), 500
    finally:
        if ftp_client:
            try:
                ftp_client.quit()
            except Exception:
                pass


@blueprint.route('/explorer/save-file', methods=['POST'])
def save_file():
    """Save text content to a remote file."""
    params = _get_connection_params()
    if not params:
        return jsonify({"status": "error", "message": "Missing connection parameters."}), 400

    hostname, port, username, password, data = params
    path = data.get('path', '')
    content = data.get('content', '')

    if not path:
        return jsonify({"status": "error", "message": "Missing file path."}), 400

    ftp_client = None

    try:
        raw = content.encode('utf-8')

        if port == 21:
            ftp_client = connect_ftp(hostname, port, username, password)
            buffer = BytesIO(raw)
            ftp_client.storbinary(f'STOR {path}', buffer)
        else:
            ssh, sftp, from_pool = _get_sftp(hostname, port, username, password)
            try:
                with sftp.open(path, 'w') as f:
                    f.write(raw)
            except Exception:
                _invalidate_pool_entry(hostname, port, username, password)
                raise
            finally:
                _release_sftp(sftp, from_pool)

        log_audit_event(
            event_type='EXPLORER_SAVE_FILE',
            status='SUCCESS',
            severity='NOTICE',
            target={'server_id': data.get('server_id'), 'hostname': hostname, 'port': port, 'resource': path},
            details={'size_bytes': len(raw)}
        )
        return jsonify({
            "status": "ok",
            "message": "File saved successfully.",
            "size": len(raw),
            "size_display": _format_size(len(raw))
        })

    except ConnectionRefusedError:
        return jsonify({"status": "error", "message": "Connection refused. Check host and port."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Save error: {str(e)}"}), 500
    finally:
        if ftp_client:
            try:
                ftp_client.quit()
            except Exception:
                pass


@blueprint.route('/explorer/create-file', methods=['POST'])
def create_file():
    """Create a new empty file (or with initial content) on the remote server."""
    params = _get_connection_params()
    if not params:
        return jsonify({"status": "error", "message": "Missing connection parameters."}), 400

    hostname, port, username, password, data = params
    path = data.get('path', '')
    content = data.get('content', '')

    if not path:
        return jsonify({"status": "error", "message": "No file path specified."}), 400

    ftp_client = None

    try:
        raw = content.encode('utf-8')

        if port == 21:
            ftp_client = connect_ftp(hostname, port, username, password)
            buffer = BytesIO(raw)
            ftp_client.storbinary(f'STOR {path}', buffer)
        else:
            ssh, sftp, from_pool = _get_sftp(hostname, port, username, password)
            try:
                # Check that the file does NOT already exist
                try:
                    sftp.stat(path)
                    return jsonify({"status": "error", "message": f"File already exists: {path}"}), 409
                except IOError:
                    pass  # expected — file doesn't exist yet

                with sftp.open(path, 'w') as f:
                    f.write(raw)
            except Exception:
                _invalidate_pool_entry(hostname, port, username, password)
                raise
            finally:
                _release_sftp(sftp, from_pool)

        log_audit_event(
            event_type='EXPLORER_CREATE_FILE',
            status='SUCCESS',
            severity='NOTICE',
            target={'server_id': data.get('server_id'), 'hostname': hostname, 'port': port, 'resource': path}
        )
        return jsonify({"status": "ok", "message": f"File created: {path}", "path": path})

    except ConnectionRefusedError:
        return jsonify({"status": "error", "message": "Connection refused. Check host and port."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error: {str(e)}"}), 500
    finally:
        if ftp_client:
            try:
                ftp_client.quit()
            except Exception:
                pass


@blueprint.route('/explorer/create-dir', methods=['POST'])
def create_dir():
    """Create a new directory on the remote server."""
    params = _get_connection_params()
    if not params:
        return jsonify({"status": "error", "message": "Missing connection parameters."}), 400

    hostname, port, username, password, data = params
    path = data.get('path', '')

    if not path:
        return jsonify({"status": "error", "message": "No directory path specified."}), 400

    ftp_client = None

    try:
        if port == 21:
            ftp_client = connect_ftp(hostname, port, username, password)
            ftp_client.mkd(path)
        else:
            ssh, sftp, from_pool = _get_sftp(hostname, port, username, password)
            try:
                sftp.mkdir(path)
            except Exception:
                _invalidate_pool_entry(hostname, port, username, password)
                raise
            finally:
                _release_sftp(sftp, from_pool)

        log_audit_event(
            event_type='EXPLORER_CREATE_DIR',
            status='SUCCESS',
            severity='NOTICE',
            target={'server_id': data.get('server_id'), 'hostname': hostname, 'port': port, 'resource': path}
        )
        return jsonify({"status": "ok", "message": f"Directory created: {path}", "path": path})

    except ConnectionRefusedError:
        return jsonify({"status": "error", "message": "Connection refused. Check host and port."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error: {str(e)}"}), 500
    finally:
        if ftp_client:
            try:
                ftp_client.quit()
            except Exception:
                pass


@blueprint.route('/explorer/delete-entry', methods=['POST'])
def delete_entry():
    """Delete a file or directory (recursively) on the remote server."""
    params = _get_connection_params()
    if not params:
        return jsonify({"status": "error", "message": "Missing connection parameters."}), 400

    hostname, port, username, password, data = params
    path = data.get('path', '')
    is_dir = data.get('is_dir', False)

    if not path or path in ('/', ''):
        return jsonify({"status": "error", "message": "Refusing to delete root path."}), 400

    ftp_client = None

    def _sftp_rmdir_recursive(sftp, remote_path):
        """Recursively remove a directory tree via SFTP."""
        for attr in sftp.listdir_attr(remote_path):
            child = remote_path.rstrip('/') + '/' + attr.filename
            if stat.S_ISDIR(attr.st_mode):
                _sftp_rmdir_recursive(sftp, child)
            else:
                sftp.remove(child)
        sftp.rmdir(remote_path)

    try:
        if port == 21:
            ftp_client = connect_ftp(hostname, port, username, password)
            if is_dir:
                ftp_client.rmd(path)
            else:
                ftp_client.delete(path)
        else:
            ssh, sftp, from_pool = _get_sftp(hostname, port, username, password)
            try:
                if is_dir:
                    _sftp_rmdir_recursive(sftp, path)
                else:
                    sftp.remove(path)
            except Exception:
                _invalidate_pool_entry(hostname, port, username, password)
                raise
            finally:
                _release_sftp(sftp, from_pool)

        log_audit_event(
            event_type='EXPLORER_DELETE_ENTRY',
            status='SUCCESS',
            severity='WARNING',
            target={'server_id': data.get('server_id'), 'hostname': hostname, 'port': port, 'resource': path},
            details={'is_dir': is_dir}
        )
        return jsonify({"status": "ok", "message": f"Deleted: {path}"})

    except ConnectionRefusedError:
        return jsonify({"status": "error", "message": "Connection refused. Check host and port."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error: {str(e)}"}), 500
    finally:
        if ftp_client:
            try:
                ftp_client.quit()
            except Exception:
                pass


@blueprint.route('/explorer/read-image', methods=['POST'])
def read_image():
    """Read an image file and return it as base64."""
    import base64
    params = _get_connection_params()
    if not params:
        return jsonify({"status": "error", "message": "Missing connection parameters."}), 400

    hostname, port, username, password, data = params
    path = data.get('path', '')
    if not path:
        return jsonify({"status": "error", "message": "No file path specified."}), 400

    ftp_client = None

    try:
        if port == 21:
            ftp_client = connect_ftp(hostname, port, username, password)
            file_size = 0
            try:
                file_size = ftp_client.size(path) or 0
            except Exception:
                pass

            if file_size > _get_max_file_size():
                return jsonify({"status": "error", "message": f"Image too large ({_format_size(file_size)}). Max: {_format_size(_get_max_file_size())}."}), 400

            buffer = BytesIO()
            ftp_client.retrbinary(f'RETR {path}', buffer.write)
            raw = buffer.getvalue()
        else:
            ssh, sftp, from_pool = _get_sftp(hostname, port, username, password)
            try:
                try:
                    file_stat = sftp.stat(path)
                    file_size = file_stat.st_size or 0
                except IOError:
                    return jsonify({"status": "error", "message": f"File not found: {path}"}), 404

                if file_size > _get_max_file_size():
                    return jsonify({"status": "error", "message": f"Image too large ({_format_size(file_size)}). Max: {_format_size(_get_max_file_size())}."}), 400

                with sftp.open(path, 'rb') as f:
                    if file_size > 0:
                        f.prefetch(file_size)
                    raw = f.read()
            except Exception:
                _invalidate_pool_entry(hostname, port, username, password)
                raise
            finally:
                _release_sftp(sftp, from_pool)

        encoded = base64.b64encode(raw).decode('utf-8')
        return jsonify({"status": "ok", "image_data": encoded})

    except ConnectionRefusedError:
        return jsonify({"status": "error", "message": "Connection refused. Check host and port."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error: {str(e)}"}), 500
    finally:
        if ftp_client:
            try:
                ftp_client.quit()
            except Exception:
                pass
