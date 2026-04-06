"""
AMIKO - Explorer Module Backend
Provides directory listing and text file reading via SFTP/FTP.
"""

from flask import Blueprint, request, jsonify
import stat
import os
from io import BytesIO
from utils.sftp_helper import connect_sftp, connect_ftp

blueprint = Blueprint('explorer', __name__)

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB limit for file reading


def _get_connection_params():
    """Extract and validate connection parameters from request."""
    data = request.get_json() or {}
    hostname = data.get('hostname')
    port = int(data.get('port', 22))
    username = data.get('username')
    password = data.get('password')

    if not all([hostname, username, password]):
        return None
    return hostname, port, username, password, data


def _format_size(size_bytes):
    """Format byte size to human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


@blueprint.route('/explorer/list-dir', methods=['POST'])
def list_dir():
    """List the contents of a remote directory."""
    params = _get_connection_params()
    if not params:
        return jsonify({"status": "error", "message": "Missing connection parameters."}), 400

    hostname, port, username, password, data = params
    path = data.get('path', '/')

    # Normalize path
    if not path or path.strip() == '':
        path = '/'

    client = None
    sftp = None

    try:
        entries = []

        if port == 21:
            # FTP mode
            client = connect_ftp(hostname, port, username, password)
            current_path = path

            try:
                client.cwd(current_path)
            except Exception:
                return jsonify({
                    "status": "error",
                    "message": f"Cannot access directory: {current_path}"
                }), 400

            # Use MLSD for detailed listing (modern FTP)
            try:
                for name, facts in client.mlsd(current_path):
                    if name in ('.', '..'):
                        continue
                    is_dir = facts.get('type', '') == 'dir'
                    size = int(facts.get('size', 0)) if not is_dir else 0
                    modified = facts.get('modify', '')
                    entries.append({
                        "name": name,
                        "is_dir": is_dir,
                        "size": size,
                        "size_display": _format_size(size) if not is_dir else "—",
                        "modified": modified
                    })
            except Exception:
                # Fallback: use NLST if MLSD is not supported
                names = client.nlst(current_path)
                for full_path_entry in names:
                    name = os.path.basename(full_path_entry)
                    if name in ('.', '..'):
                        continue
                    # Try to determine if directory
                    is_dir = False
                    size = 0
                    try:
                        client.cwd(full_path_entry)
                        is_dir = True
                        client.cwd(current_path)
                    except Exception:
                        try:
                            size = client.size(full_path_entry) or 0
                        except Exception:
                            size = 0
                    entries.append({
                        "name": name,
                        "is_dir": is_dir,
                        "size": size,
                        "size_display": _format_size(size) if not is_dir else "—",
                        "modified": ""
                    })
        else:
            # SFTP mode
            client, sftp = connect_sftp(hostname, port, username, password)

            try:
                file_list = sftp.listdir_attr(path)
            except IOError:
                return jsonify({
                    "status": "error",
                    "message": f"Cannot access directory: {path}"
                }), 400

            for attr in file_list:
                name = attr.filename
                if name in ('.', '..'):
                    continue
                is_dir = stat.S_ISDIR(attr.st_mode) if attr.st_mode else False
                size = attr.st_size or 0
                modified = attr.st_mtime or 0

                entries.append({
                    "name": name,
                    "is_dir": is_dir,
                    "size": size,
                    "size_display": _format_size(size) if not is_dir else "—",
                    "modified": modified
                })

        # Sort: directories first, then alphabetically
        entries.sort(key=lambda e: (not e['is_dir'], e['name'].lower()))

        return jsonify({
            "status": "ok",
            "path": path,
            "entries": entries
        })

    except ConnectionRefusedError:
        return jsonify({
            "status": "error",
            "message": "Connection refused. Check host and port."
        }), 500
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error: {str(e)}"
        }), 500
    finally:
        if sftp:
            sftp.close()
        if client:
            if port == 21:
                try:
                    client.quit()
                except Exception:
                    pass
            else:
                client.close()


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

    client = None
    sftp = None

    try:
        filename = os.path.basename(path)
        content = None
        file_size = 0

        if port == 21:
            # FTP mode
            client = connect_ftp(hostname, port, username, password)

            # Check file size first
            try:
                file_size = client.size(path) or 0
            except Exception:
                file_size = 0

            if file_size > MAX_FILE_SIZE:
                return jsonify({
                    "status": "error",
                    "message": f"File too large ({_format_size(file_size)}). Max: {_format_size(MAX_FILE_SIZE)}."
                }), 400

            # Download file content into memory
            buffer = BytesIO()
            client.retrbinary(f'RETR {path}', buffer.write)
            raw = buffer.getvalue()

        else:
            # SFTP mode
            client, sftp = connect_sftp(hostname, port, username, password)

            # Check file size first
            try:
                file_stat = sftp.stat(path)
                file_size = file_stat.st_size or 0
            except IOError:
                return jsonify({
                    "status": "error",
                    "message": f"File not found: {path}"
                }), 404

            if file_size > MAX_FILE_SIZE:
                return jsonify({
                    "status": "error",
                    "message": f"File too large ({_format_size(file_size)}). Max: {_format_size(MAX_FILE_SIZE)}."
                }), 400

            # Read file content
            with sftp.open(path, 'r') as f:
                raw = f.read()

        # Try to decode as text
        try:
            content = raw.decode('utf-8')
        except (UnicodeDecodeError, AttributeError):
            try:
                content = raw.decode('latin-1')
            except Exception:
                return jsonify({
                    "status": "error",
                    "message": "Cannot decode file: it appears to be a binary file."
                }), 400

        return jsonify({
            "status": "ok",
            "filename": filename,
            "path": path,
            "content": content,
            "size": file_size,
            "size_display": _format_size(file_size)
        })

    except ConnectionRefusedError:
        return jsonify({
            "status": "error",
            "message": "Connection refused. Check host and port."
        }), 500
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Error: {str(e)}"
        }), 500
    finally:
        if sftp:
            sftp.close()
        if client:
            if port == 21:
                try:
                    client.quit()
                except Exception:
                    pass
            else:
                client.close()


@blueprint.route('/explorer/save-file', methods=['POST'])
def save_file():
    """Save text content to a remote file."""
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No data provided."}), 400

    hostname = data.get('hostname')
    port = int(data.get('port', 22))
    username = data.get('username')
    password = data.get('password')
    path = data.get('path', '')
    content = data.get('content', '')

    if not all([hostname, username, password, path]):
        return jsonify({"status": "error", "message": "Missing parameters."}), 400

    client = None
    sftp = None

    try:
        raw = content.encode('utf-8')

        if port == 21:
            # FTP mode
            client = connect_ftp(hostname, port, username, password)
            buffer = BytesIO(raw)
            client.storbinary(f'STOR {path}', buffer)
        else:
            # SFTP mode
            client, sftp = connect_sftp(hostname, port, username, password)
            with sftp.open(path, 'w') as f:
                f.write(raw)

        return jsonify({
            "status": "ok",
            "message": "File saved successfully.",
            "size": len(raw),
            "size_display": _format_size(len(raw))
        })

    except ConnectionRefusedError:
        return jsonify({
            "status": "error",
            "message": "Connection refused. Check host and port."
        }), 500
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Save error: {str(e)}"
        }), 500
    finally:
        if sftp:
            sftp.close()
        if client:
            if port == 21:
                try:
                    client.quit()
                except Exception:
                    pass
            else:
                client.close()


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

    client = None
    sftp = None

    try:
        if port == 21:
            # FTP mode
            client = connect_ftp(hostname, port, username, password)
            file_size = 0
            try:
                file_size = client.size(path) or 0
            except Exception:
                pass
            
            if file_size > MAX_FILE_SIZE:
                return jsonify({
                    "status": "error",
                    "message": f"Image too large ({_format_size(file_size)}). Max: {_format_size(MAX_FILE_SIZE)}."
                }), 400
            
            buffer = BytesIO()
            client.retrbinary(f'RETR {path}', buffer.write)
            raw = buffer.getvalue()
        else:
            # SFTP mode
            client, sftp = connect_sftp(hostname, port, username, password)
            try:
                file_stat = sftp.stat(path)
                file_size = file_stat.st_size or 0
            except IOError:
                return jsonify({"status": "error", "message": f"File not found: {path}"}), 404
            
            if file_size > MAX_FILE_SIZE:
                return jsonify({
                    "status": "error",
                    "message": f"Image too large ({_format_size(file_size)}). Max: {_format_size(MAX_FILE_SIZE)}."
                }), 400
                
            with sftp.open(path, 'rb') as f:
                raw = f.read()

        encoded = base64.b64encode(raw).decode('utf-8')
        return jsonify({
            "status": "ok",
            "image_data": encoded
        })

    except ConnectionRefusedError:
        return jsonify({"status": "error", "message": "Connection refused. Check host and port."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error: {str(e)}"}), 500
    finally:
        if sftp:
            sftp.close()
        if client:
            if port == 21:
                try:
                    client.quit()
                except Exception:
                    pass
            else:
                client.close()
