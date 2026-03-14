"""
SFTP and SSH operations helper module
Reusable functions following DRY principles
"""

import paramiko
import stat
import os
import json
import ftplib
from io import BytesIO


def connect_ftp(hostname, port, username, password, timeout=10):
    """
    Establishes FTP connection.
    
    Returns:
        ftplib.FTP: FTP client instance
    """
    ftp = ftplib.FTP()
    ftp.connect(hostname, int(port), timeout=timeout)
    ftp.login(username, password)
    return ftp


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


def connect_sftp(hostname, port, username, password, timeout=10):
    """
    Establishes SFTP connection.
    
    Returns:
        tuple: (ssh_client, sftp_client) or raises exception
    """
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname, port=int(port), username=username, password=password, timeout=timeout)
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
    Get system statistics (CPU, RAM, Disk) via SSH.
    
    Args:
        ssh: SSH client instance
    
    Returns:
        dict: {'cpu': float, 'ram': float, 'disk': float, 'network': dict}
    """
    stats = {
        'cpu': 0.0,
        'ram': 0.0,
        'disk': 0.0,
        'network': {'rx': 0, 'tx': 0}
    }
    
    try:
        # CPU usage (percentage)
        cpu_cmd = "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | sed 's/%us,//'"
        result = execute_ssh_command(ssh, cpu_cmd)
        if result['exit_code'] == 0 and result['stdout'].strip():
            try:
                stats['cpu'] = float(result['stdout'].strip().replace('%', '').replace(',', '.'))
            except ValueError:
                pass
        
        # RAM usage (percentage)
        ram_cmd = "free | grep Mem | awk '{print ($3/$2) * 100.0}'"
        result = execute_ssh_command(ssh, ram_cmd)
        if result['exit_code'] == 0 and result['stdout'].strip():
            try:
                stats['ram'] = float(result['stdout'].strip())
            except ValueError:
                pass
        
        # Disk usage (percentage) - root partition
        disk_cmd = "df -h / | tail -1 | awk '{print $5}' | sed 's/%//'"
        result = execute_ssh_command(ssh, disk_cmd)
        if result['exit_code'] == 0 and result['stdout'].strip():
            try:
                stats['disk'] = float(result['stdout'].strip())
            except ValueError:
                pass
        
        # Network stats (bytes)
        # Read from /proc/net/dev for eth0 or fallback to first non-lo interface
        net_cmd = "cat /proc/net/dev | awk 'NR>2 {print $1, $2, $10}'" # Interface, RX, TX
        result = execute_ssh_command(ssh, net_cmd)
        if result['exit_code'] == 0 and result['stdout'].strip():
            lines = result['stdout'].strip().split('\n')
            total_rx = 0
            total_tx = 0
            for line in lines:
                parts = line.split()
                if len(parts) >= 3:
                    interface = parts[0].strip(':')
                    if interface != 'lo':
                        try:
                            total_rx += int(parts[1])
                            total_tx += int(parts[2])
                        except ValueError:
                            pass
            
            stats['network'] = {'rx': total_rx, 'tx': total_tx}
        
    except Exception as e:
        # Return default stats on error
        pass
    
    return stats
