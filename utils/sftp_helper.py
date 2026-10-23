"""
SFTP and SSH operations helper module
Reusable functions following DRY principles
"""

import paramiko
import stat
import os
import json
import base64
import hashlib
import threading
from io import BytesIO, StringIO


def log_audit_event(*args, **kwargs):
    from core.audit import log_audit_event as _log_audit_event
    return _log_audit_event(*args, **kwargs)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
KNOWN_HOSTS_FILE = os.path.join(DATA_DIR, "known_hosts")
_known_hosts_lock = threading.Lock()


def compute_host_key_fingerprint(key: paramiko.PKey) -> str:
    """Return OpenSSH-style SHA256 fingerprint (e.g. SHA256:xxxx)."""
    digest = hashlib.sha256(key.asbytes()).digest()
    b64 = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{b64}"


class ManagedHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """
    Pins the server's SSH host key into data/known_hosts on first connection
    and records the cryptographic SHA-256 fingerprint in the immutable audit log.
    Subsequent connections strictly verify the pinned key via Paramiko's host_keys.
    """

    def missing_host_key(self, client, hostname, key):
        fingerprint = compute_host_key_fingerprint(key)
        key_type = key.get_name()

        with _known_hosts_lock:
            os.makedirs(DATA_DIR, exist_ok=True)
            client.get_host_keys().add(hostname, key_type, key)
            try:
                client.save_host_keys(KNOWN_HOSTS_FILE)
                os.chmod(KNOWN_HOSTS_FILE, 0o600)
            except Exception as e:
                print(f"[SSH Security] Failed to save known_hosts: {e}")

        log_audit_event(
            event_type="SSH_HOST_KEY_PINNED",
            status="SUCCESS",
            severity="NOTICE",
            target={"hostname": hostname},
            details={"key_type": key_type, "fingerprint": fingerprint}
        )


def get_known_hosts_list() -> list[dict]:
    """Return all pinned SSH host keys from data/known_hosts."""
    if not os.path.exists(KNOWN_HOSTS_FILE):
        return []
    result = []
    with _known_hosts_lock:
        try:
            hk = paramiko.HostKeys(KNOWN_HOSTS_FILE)
            for host in hk.keys():
                sub = hk[host]
                for key_type in sub.keys():
                    pkey = sub[key_type]
                    result.append({
                        "host": host,
                        "key_type": key_type,
                        "fingerprint": compute_host_key_fingerprint(pkey)
                    })
        except Exception:
            return []
    return result


def remove_known_host(host_entry: str) -> bool:
    """Remove a pinned host entry from data/known_hosts (Admin action)."""
    if not os.path.exists(KNOWN_HOSTS_FILE) or not host_entry:
        return False
    with _known_hosts_lock:
        try:
            hk = paramiko.HostKeys(KNOWN_HOSTS_FILE)
            if host_entry not in hk:
                return False
            del hk[host_entry]
            hk.save(KNOWN_HOSTS_FILE)
            return True
        except Exception:
            return False


def load_private_key_from_pem(private_key_pem: str, passphrase: str = None) -> paramiko.PKey:
    """Parse an Ed25519, RSA, or ECDSA private key from a PEM string in memory."""
    pem_text = (private_key_pem or "").strip()
    if not pem_text:
        raise ValueError("Empty SSH private key")

    pw = passphrase if passphrase else None
    key_classes = []
    for attr in ("Ed25519Key", "RSAKey", "ECDSAKey"):
        cls = getattr(paramiko, attr, None)
        if cls is not None:
            key_classes.append(cls)

    last_err = None
    for cls in key_classes:
        try:
            return cls.from_private_key(StringIO(pem_text), password=pw)
        except Exception as e:
            last_err = e
            continue

    raise ValueError(f"Unsupported or invalid SSH private key: {last_err}")


def connect_ftp(hostname, port, username, password, timeout=10):
    """
    Plain-text FTP (port 21) is disabled to prevent cleartext credential transmission.
    """
    log_audit_event(
        event_type="INSECURE_PROTOCOL_BLOCKED",
        status="DENIED",
        severity="WARNING",
        target={"hostname": hostname, "port": int(port or 21)},
        details={"protocol": "FTP", "reason": "Plain-text FTP is disabled; use SFTP/SSH"}
    )
    raise PermissionError("Plain-text FTP (port 21) is disabled for security. Please use encrypted SFTP/SSH.")


def search_keyword_in_files_ftp(ftp, remote_path, keyword, case_sensitive=False, extensions=None):
    """
    Search for keyword in files recursively via FTP.
    """
    if extensions is None:
        extensions = ['.php', '.py', '.html', '.sh', '.js', '.txt', '.css', '.json', '.xml']
    
    files_checked = 0
    matches_found = 0
    
    def walk_and_search(path):
        nonlocal files_checked, matches_found
        
        try:
            # List directory
            items = []
            try:
                ftp.cwd(path)
                items = ftp.mlsd() # Try MLSD first (modern)
            except ftplib.error_perm:
                # Fallback to NLST or LIST if MLSD not supported
                # This is tricky with FTP, for now assume MLSD supported or handle basic
                # If MLSD fails, simple recursion might be hard without type info
                # Simple fallback: try to re-login or just log error
                yield {
                    'type': 'error',
                    'message': f'Server FTP non supporta comando MLSD in {path}'
                }
                return

            for name, facts in items:
                if name in ['.', '..']:
                    continue
                    
                full_path = f"{path}/{name}" if path != '/' else f"/{name}"
                type_fact = facts.get('type')
                
                if type_fact == 'dir':
                    yield from walk_and_search(full_path)
                    # Reset CWD after recursion
                    ftp.cwd(path)
                    
                elif type_fact == 'file':
                    ext = os.path.splitext(name)[1].lower()
                    if ext in extensions:
                        try:
                            # Read file content
                            bio = BytesIO()
                            
                            def handle_binary(data):
                                bio.write(data)
                                
                            ftp.retrbinary(f'RETR {name}', handle_binary)
                            content = bio.getvalue().decode('utf-8', errors='ignore')
                            
                            found = False
                            lines = content.splitlines()
                            line_num = 0
                            
                            for line in lines:
                                line_num += 1
                                content_to_check = line
                                keyword_to_check = keyword
                                
                                if not case_sensitive:
                                    content_to_check = line.lower()
                                    keyword_to_check = keyword.lower()
                                
                                if keyword_to_check in content_to_check:
                                    found = True
                                    matches_found += 1
                                    yield {
                                        'type': 'match',
                                        'message': f'CORRISPONDENZA: {full_path} (Riga {line_num})',
                                        'file': full_path,
                                        'line': line_num
                                    }
                            
                            files_checked += 1
                            if not found:
                                yield {
                                    'type': 'log',
                                    'message': f'Controllato {full_path}: Nessuna corrispondenza'
                                }
                                
                        except Exception as e:
                            yield {
                                'type': 'error',
                                'message': f'Errore lettura {full_path}: {str(e)}'
                            }

        except Exception as e:
            yield {
                'type': 'error',
                'message': f'Errore accesso percorso {path}: {str(e)}'
            }

    yield from walk_and_search(remote_path)
    
    yield {
        'type': 'summary',
        'files_checked': files_checked,
        'matches_found': matches_found
    }


def search_large_files_ftp(ftp, remote_path, threshold_mb):
    """
    Search for large files via FTP.
    """
    threshold_bytes = threshold_mb * 1024 * 1024
    files_checked = 0
    large_files_found = 0
    
    def walk_and_find(path):
        nonlocal files_checked, large_files_found
        
        try:
            ftp.cwd(path)
            items = ftp.mlsd()

            for name, facts in items:
                if name in ['.', '..']:
                    continue
                
                full_path = f"{path}/{name}" if path != '/' else f"/{name}"
                type_fact = facts.get('type')
                size_fact = facts.get('size')
                
                if type_fact == 'dir':
                    yield from walk_and_find(full_path)
                    ftp.cwd(path)
                    
                elif type_fact == 'file' and size_fact:
                    files_checked += 1
                    file_size = int(size_fact)
                    size_mb = file_size / (1024 * 1024)
                    
                    if file_size >= threshold_bytes:
                        large_files_found += 1
                        yield {
                            'type': 'match',
                            'message': f'FILE PESANTE: {full_path} ({size_mb:.2f} MB)',
                            'file': full_path,
                            'size_bytes': file_size,
                            'size_mb': size_mb
                        }
                    else:
                        yield {
                            'type': 'log',
                            'message': f'Controllato {full_path}: {size_mb:.2f} MB'
                        }
                        
        except Exception as e:
            yield {
                'type': 'error',
                'message': f'Errore accesso percorso {path}: {str(e)}'
            }
            
    yield from walk_and_find(remote_path)
    
    yield {
        'type': 'summary',
        'files_checked': files_checked,
        'large_files_found': large_files_found
    }


def connect_ssh_client(hostname, port, username, password="", private_key="", timeout=10):
    """
    Establishes a hardened SSH connection with:
      - Managed known_hosts verification (rejects modified host keys to prevent MitM)
      - Support for asymmetric SSH private keys (Ed25519, RSA, ECDSA) and/or passwords
    """
    port_int = int(port or 22)
    if port_int == 21:
        connect_ftp(hostname, port_int, username, password, timeout=timeout)

    ssh = paramiko.SSHClient()
    with _known_hosts_lock:
        if os.path.exists(KNOWN_HOSTS_FILE):
            try:
                ssh.load_host_keys(KNOWN_HOSTS_FILE)
            except Exception as e:
                print(f"[SSH Security] Warning loading known_hosts: {e}")
    ssh.set_missing_host_key_policy(ManagedHostKeyPolicy())

    pkey = None
    if private_key and str(private_key).strip():
        pkey = load_private_key_from_pem(private_key, passphrase=password if password else None)
    elif isinstance(password, str) and "-----BEGIN " in password and "PRIVATE KEY-----" in password:
        pkey = load_private_key_from_pem(password, passphrase=None)

    try:
        connect_kwargs = {
            "hostname": hostname,
            "port": port_int,
            "username": username,
            "timeout": timeout,
        }
        if pkey is not None:
            connect_kwargs["pkey"] = pkey
            connect_kwargs["allow_agent"] = False
            connect_kwargs["look_for_keys"] = False
        else:
            connect_kwargs["password"] = password if password else None
            connect_kwargs["passphrase"] = password if password else None
            connect_kwargs["allow_agent"] = True
            connect_kwargs["look_for_keys"] = True

        ssh.connect(**connect_kwargs)
        return ssh
    except paramiko.BadHostKeyException as e:
        expected_fp = compute_host_key_fingerprint(e.expected_key) if getattr(e, "expected_key", None) else "unknown"
        got_fp = compute_host_key_fingerprint(e.key) if getattr(e, "key", None) else "unknown"
        log_audit_event(
            event_type="SSH_HOST_KEY_MISMATCH",
            status="DENIED",
            severity="CRITICAL",
            target={"hostname": hostname, "port": port_int},
            details={"expected_fingerprint": expected_fp, "received_fingerprint": got_fp}
        )
        raise PermissionError(
            f"SSH Host Key verification failed for {hostname}:{port_int}! "
            f"Expected {expected_fp}, got {got_fp}. Connection blocked to prevent Man-in-the-Middle."
        )


def connect_sftp(hostname, port, username, password="", private_key="", timeout=10):
    """
    Establishes hardened SFTP connection over SSH.
    
    Returns:
        tuple: (ssh_client, sftp_client) or raises exception
    """
    ssh = connect_ssh_client(
        hostname=hostname,
        port=port,
        username=username,
        password=password,
        private_key=private_key,
        timeout=timeout
    )
    sftp = ssh.open_sftp()
    return ssh, sftp


def search_keyword_in_files(sftp, remote_path, keyword, case_sensitive=False, extensions=None):
    """
    Search for keyword in files recursively via SFTP.
    
    Args:
        sftp: SFTP client instance
        remote_path: String path to search
        keyword: String to search for
        case_sensitive: Boolean for case sensitivity
        extensions: List of file extensions to check (default: common text files)
    
    Yields:
        dict: Event dictionaries with type and message
    """
    if extensions is None:
        extensions = ['.php', '.py', '.html', '.sh', '.js', '.txt', '.css', '.json', '.xml']
    
    files_checked = 0
    matches_found = 0
    
    def walk_and_search(path):
        nonlocal files_checked, matches_found
        
        try:
            for entry in sftp.listdir_attr(path):
                remote_file_path = os.path.join(path, entry.filename)
                mode = entry.st_mode
                
                if stat.S_ISDIR(mode):
                    # Recursive call for directories
                    yield from walk_and_search(remote_file_path)
                    
                elif stat.S_ISREG(mode):
                    # Check extension
                    ext = os.path.splitext(entry.filename)[1].lower()
                    if ext in extensions:
                        try:
                            found = False
                            with sftp.open(remote_file_path, 'r') as f:
                                line_num = 0
                                try:
                                    for line in f:
                                        line_num += 1
                                        
                                        content_to_check = line
                                        keyword_to_check = keyword
                                        
                                        if not case_sensitive:
                                            content_to_check = line.lower()
                                            keyword_to_check = keyword.lower()
                                        
                                        if keyword_to_check in content_to_check:
                                            found = True
                                            matches_found += 1
                                            yield {
                                                'type': 'match',
                                                'message': f'CORRISPONDENZA TROVATA: {remote_file_path} (Riga {line_num})',
                                                'file': remote_file_path,
                                                'line': line_num
                                            }
                                            
                                except UnicodeDecodeError:
                                    pass  # Skip binary files
                            
                            files_checked += 1
                            if not found:
                                yield {
                                    'type': 'log',
                                    'message': f'Controllato {remote_file_path}: Nessuna corrispondenza'
                                }
                        
                        except Exception as e:
                            yield {
                                'type': 'error',
                                'message': f'Errore lettura {remote_file_path}: {str(e)}'
                            }
        
        except Exception as e:
            yield {
                'type': 'error',
                'message': f'Errore accesso percorso {path}: {str(e)}'
            }
    
    yield from walk_and_search(remote_path)
    
    yield {
        'type': 'summary',
        'files_checked': files_checked,
        'matches_found': matches_found
    }


def search_large_files(sftp, remote_path, threshold_mb):
    """
    Search for files larger than threshold size recursively via SFTP.
    
    Args:
        sftp: SFTP client instance
        remote_path: String path to search
        threshold_mb: Float minimum size in megabytes
    
    Yields:
        dict: Event dictionaries with type, message, and file info
    """
    threshold_bytes = threshold_mb * 1024 * 1024
    files_checked = 0
    large_files_found = 0
    
    def walk_and_find(path):
        nonlocal files_checked, large_files_found
        
        try:
            for entry in sftp.listdir_attr(path):
                remote_file_path = os.path.join(path, entry.filename)
                mode = entry.st_mode
                
                if stat.S_ISDIR(mode):
                    yield from walk_and_find(remote_file_path)
                    
                elif stat.S_ISREG(mode):
                    files_checked += 1
                    file_size = entry.st_size
                    size_mb = file_size / (1024 * 1024)
                    
                    if file_size >= threshold_bytes:
                        large_files_found += 1
                        yield {
                            'type': 'match',
                            'message': f'FILE PESANTE: {remote_file_path} ({size_mb:.2f} MB)',
                            'file': remote_file_path,
                            'size_bytes': file_size,
                            'size_mb': size_mb
                        }
                    else:
                        yield {
                            'type': 'log',
                            'message': f'Controllato {remote_file_path}: {size_mb:.2f} MB'
                        }
        
        except Exception as e:
            yield {
                'type': 'error',
                'message': f'Errore accesso percorso {path}: {str(e)}'
            }
    
    yield from walk_and_find(remote_path)
    
    yield {
        'type': 'summary',
        'files_checked': files_checked,
        'large_files_found': large_files_found
    }


def execute_ssh_command(ssh, command):
    """
    Execute a command via SSH and return output.
    
    Args:
        ssh: SSH client instance
        command: String command to execute
    
    Returns:
        dict: {'stdout': str, 'stderr': str, 'exit_code': int}
    """
    stdin, stdout, stderr = ssh.exec_command(command)
    exit_code = stdout.channel.recv_exit_status()
    
    return {
        'stdout': stdout.read().decode('utf-8', errors='ignore'),
        'stderr': stderr.read().decode('utf-8', errors='ignore'),
        'exit_code': exit_code
    }


def get_system_stats(ssh):
    """
    Get system statistics (CPU, RAM, Disk, Network) via a single combined SSH command
    to minimize channel overhead and avoid blocking interactive SFTP/Explorer requests.
    """
    stats = {
        'cpu': 0.0,
        'ram': 0.0,
        'disk': 0.0,
        'network': {'rx': 0, 'tx': 0}
    }

    combined_cmd = (
        "LC_ALL=C sh -c '"
        "read _ a b c d e f g _ < /proc/stat; t1=$((a+b+c+d+e+f+g)); i1=$((d+e)); "
        "sleep 0.1; "
        "read _ a b c d e f g _ < /proc/stat; t2=$((a+b+c+d+e+f+g)); i2=$((d+e)); "
        "dt=$((t2-t1)); di=$((i2-i1)); "
        "if [ \"$dt\" -gt 0 ]; then awk \"BEGIN{printf \\\"CPU:%.1f\\\\n\\\", (($dt-$di)*100.0)/$dt}\"; else echo \"CPU:0.0\"; fi; "
        "free | awk \"/^Mem:/{if(\\$2>0) printf \\\"RAM:%.1f\\\\n\\\", (\\$3*100.0)/\\$2}\"; "
        "df -P / | awk \"NR==2{gsub(/%/,\\\"\\\",\\$5); printf \\\"DISK:%s\\\\n\\\", \\$5}\"; "
        "awk \"NR>2 && \\$1!=\\\"lo:\\\"{rx+=\\$2; tx+=\\$10} END{printf \\\"NET:%d %d\\\\n\\\", rx, tx}\" /proc/net/dev"
        "'"
    )

    try:
        result = execute_ssh_command(ssh, combined_cmd)
        if result['exit_code'] == 0 and result['stdout']:
            for line in result['stdout'].splitlines():
                line = line.strip()
                if line.startswith('CPU:'):
                    try:
                        stats['cpu'] = float(line[4:].strip())
                    except ValueError:
                        pass
                elif line.startswith('RAM:'):
                    try:
                        stats['ram'] = float(line[4:].strip())
                    except ValueError:
                        pass
                elif line.startswith('DISK:'):
                    try:
                        stats['disk'] = float(line[5:].strip())
                    except ValueError:
                        pass
                elif line.startswith('NET:'):
                    parts = line[4:].strip().split()
                    if len(parts) == 2:
                        try:
                            stats['network'] = {'rx': int(parts[0]), 'tx': int(parts[1])}
                        except ValueError:
                            pass
    except Exception:
        pass

    return stats
