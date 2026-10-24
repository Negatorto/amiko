import os
import json
import time
from flask import render_template, request, Response, stream_with_context, jsonify, send_from_directory, session, redirect, url_for

from core import app, active_modules
from core.config import settings, ssh_sessions, MODULES_DIR
from core.auth import get_current_user, filter_modules_for_user, role_required
from core.audit import (
    log_audit_event,
    get_audit_events,
    verify_audit_chain,
    check_login_lockout,
    record_login_attempt,
    AUDIT_LOG_FILE
)
from utils.sftp_helper import (
    connect_sftp,
    execute_ssh_command,
    get_system_stats,
    get_known_hosts_list,
    remove_known_host
)

@app.route('/login', methods=['GET'])
def login_page():
    """Login screen"""
    if get_current_user():
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password are required.'}), 400

    source_ip = (
        request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
        or request.remote_addr
        or 'unknown'
    )

    locked, rem_seconds = check_login_lockout(username, source_ip)
    if locked:
        log_audit_event(
            event_type='AUTH_LOCKOUT',
            status='DENIED',
            severity='ALERT',
            actor={'username': username, 'source_ip': source_ip, 'role': 'none'},
            details={'reason': 'Account/IP locked due to repeated failed attempts', 'remaining_seconds': rem_seconds}
        )
        return jsonify({
            'success': False,
            'error': f'Too many failed attempts. Try again in {rem_seconds}s.'
        }), 429

    user = settings.verify_user(username, password)
    if not user:
        just_locked, lock_rem, fail_count = record_login_attempt(username, source_ip, success=False)
        if just_locked:
            log_audit_event(
                event_type='AUTH_LOCKOUT',
                status='DENIED',
                severity='ALERT',
                actor={'username': username, 'source_ip': source_ip, 'role': 'none'},
                details={'failed_attempts': fail_count, 'lockout_seconds': lock_rem}
            )
            return jsonify({
                'success': False,
                'error': f'Too many failed login attempts ({fail_count}). Locked for {lock_rem}s.'
            }), 429

        log_audit_event(
            event_type='AUTH_LOGIN_FAILED',
            status='FAILURE',
            severity='WARNING',
            actor={'username': username, 'source_ip': source_ip, 'role': 'none'},
            details={'failed_attempts': fail_count}
        )
        return jsonify({'success': False, 'error': 'Invalid username or password.'}), 401

    record_login_attempt(username, source_ip, success=True)
    session.clear()
    session['user_id'] = user['id']
    current = get_current_user()
    log_audit_event(
        event_type='AUTH_LOGIN_SUCCESS',
        status='SUCCESS',
        severity='INFO',
        actor={'user_id': user['id'], 'username': user['username'], 'role': user['role'], 'source_ip': source_ip}
    )
    return jsonify({'success': True, 'user': current})


@app.route('/api/auth/logout', methods=['POST'])
def api_auth_logout():
    user = get_current_user()
    if user:
        log_audit_event(
            event_type='AUTH_LOGOUT',
            status='SUCCESS',
            severity='INFO',
            actor={'user_id': user['id'], 'username': user['username'], 'role': user['role']}
        )
    session.clear()
    return jsonify({'success': True})


@app.route('/api/auth/me', methods=['GET'])
def api_auth_me():
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Not authenticated'}), 401
    return jsonify(user)


@app.route('/static_modules/<module_name>/<path:filename>')
def serve_module_static(module_name, filename):
    """Serve static files (like client.js) from module folders"""
    user = get_current_user()
    if user and module_name in user.get('capabilities', {}).get('blocked_modules', []):
        return jsonify({'error': 'Module access denied for your role'}), 403
    module_dir = os.path.join(MODULES_DIR, module_name)
    return send_from_directory(module_dir, filename)

@app.route('/')
def index():
    """Main page filtered by user's Least Privilege role"""
    user = get_current_user()
    user_modules = filter_modules_for_user(active_modules, user)
    return render_template('index.html', modules=user_modules, current_user=user)

@app.route('/ssh-connect', methods=['POST'])
def ssh_connect():
    """
    Monitor 1: Establish SSH connection (resolves credentials server-side from vault when server_id is used)
    """
    data = request.get_json(silent=True) or {}
    user = get_current_user()
    resolved = settings.resolve_connection_credentials(data, user=user, ssh_sessions=ssh_sessions)

    if not resolved:
        return jsonify({'success': False, 'error': 'Missing credentials or server not found in vault'})

    hostname = resolved['hostname']
    port = resolved['port']
    username = resolved['username']
    password = resolved['password']
    private_key = resolved.get('private_key', '')
    server_id = resolved.get('server_id')
    server_name = resolved.get('server_name', '')
    group_id = resolved.get('group_id')

    try:
        # Connect using hardened SFTP/SSH utility
        ssh, _ = connect_sftp(hostname, port, username, password, private_key=private_key)
        
        # Determine initial CWD
        stdin, stdout, stderr = ssh.exec_command("pwd")
        initial_cwd = stdout.read().decode('utf-8').strip()
        
        # Generate session ID
        session_id = f"{hostname}_{username}_{int(time.time())}"

        # If an admin explicitly typed a new password while connecting to a saved server, persist it in the vault
        typed_pw = data.get('password') or ''
        if server_id and typed_pw and user and user.get('role') == 'admin':
            settings.update_server(server_id, password=typed_pw)
        
        # Store SSH connection server-side
        ssh_sessions[session_id] = {
            'ssh': ssh,
            'hostname': hostname,
            'port': port,
            'username': username,
            'password': password,
            'private_key': private_key,
            'server_id': server_id,
            'server_name': server_name,
            'group_id': group_id,
            'user_id': user['id'] if user else None,
            'cwd': initial_cwd
        }
        
        log_audit_event(
            event_type='SSH_CONNECT',
            status='SUCCESS',
            severity='INFO',
            target={'server_id': server_id, 'hostname': hostname, 'port': port},
            details={'ssh_username': username, 'session_id': session_id, 'server_name': server_name}
        )
        return jsonify({
            'success': True,
            'session_id': session_id,
            'server_id': server_id,
            'server_name': server_name,
            'group_id': group_id,
            'hostname': hostname,
            'port': port,
            'username': username
        })
        
    except Exception as e:
        log_audit_event(
            event_type='SSH_CONNECT',
            status='FAILURE',
            severity='WARNING',
            target={'server_id': server_id, 'hostname': hostname, 'port': port},
            details={'ssh_username': username, 'error': str(e)}
        )
        return jsonify({'success': False, 'error': str(e)})


@app.route('/ssh-command', methods=['POST'])
def ssh_command():
    """
    Monitor 1: Execute SSH command
    """
    data = request.get_json(silent=True) or {}
    session_id = data.get('session_id')
    command = data.get('command')

    if not command:
        return jsonify({'success': False, 'error': 'Missing command'})

    ssh = None
    session_data = None

    # Try to reuse existing session
    if session_id and session_id in ssh_sessions:
        session_data = ssh_sessions[session_id]
        ssh = session_data['ssh']
    
    # If no session or invalid, try to reconnect resolving credentials from vault/session
    if not ssh:
        resolved = settings.resolve_connection_credentials(data, user=get_current_user(), ssh_sessions=ssh_sessions)
        if resolved:
            try:
                hostname = resolved['hostname']
                port = resolved['port']
                username = resolved['username']
                password = resolved['password']
                private_key = resolved.get('private_key', '')
                ssh, sftp = connect_sftp(hostname, port, username, password, private_key=private_key)
                
                # Determine initial CWD
                stdin, stdout, stderr = ssh.exec_command("pwd")
                initial_cwd = stdout.read().decode('utf-8').strip()
                
                # Create new session
                new_session_id = f"{hostname}_{username}_{int(time.time())}"
                ssh_sessions[new_session_id] = {
                    'ssh': ssh,
                    'hostname': hostname,
                    'port': port,
                    'username': username,
                    'password': password,
                    'private_key': private_key,
                    'server_id': resolved.get('server_id'),
                    'group_id': resolved.get('group_id'),
                    'sftp': sftp,
                    'cwd': initial_cwd
                }
                session_id = new_session_id
                session_data = ssh_sessions[new_session_id]
            except Exception as e:
                return jsonify({'success': False, 'error': f'Reconnection failed: {str(e)}'})

    if not ssh:
         return jsonify({'success': False, 'error': 'No active session and no credentials provided'})

    try:
        # Wrap command to maintain state (CWD)
        current_cwd = session_data.get('cwd', '/root')
        
        # Delimiter to separate command output from pwd output
        delimiter = "___PWD_DELIMITER___"
        
        wrapped_command = f"cd \"{current_cwd}\" && {command}; echo '{delimiter}'; pwd"
        result = execute_ssh_command(ssh, wrapped_command)
        
        full_stdout = result['stdout']
        cmd_output = full_stdout
        new_cwd = current_cwd
        
        # Parse output to separate command output and new CWD
        if delimiter in full_stdout:
            parts = full_stdout.split(delimiter)
            
            if len(parts) > 1:
                potential_cwd = parts[1].strip()
                if potential_cwd:
                    new_cwd = potential_cwd
                    session_data['cwd'] = new_cwd
        
        log_audit_event(
            event_type='SSH_COMMAND',
            status='SUCCESS' if result['exit_code'] == 0 else 'FAILURE',
            severity='NOTICE',
            target={
                'server_id': session_data.get('server_id'),
                'hostname': session_data.get('hostname'),
                'port': session_data.get('port', 22)
            },
            details={'command': command, 'cwd': new_cwd, 'exit_code': result['exit_code']}
        )
        return jsonify({
            'success': True,
            'stdout': cmd_output,
            'stderr': result['stderr'],
            'exit_code': result['exit_code'],
            'session_id': session_id,
            'cwd': new_cwd
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/ssh-stats')
def ssh_stats():
    """
    Monitor 1: Stream system stats via SSE
    """
    session_id = request.args.get('session_id')

    if not session_id or session_id not in ssh_sessions:
        return "Missing or invalid session", 400
        
    session_data = ssh_sessions[session_id]
    hostname = session_data['hostname']
    port = session_data['port']
    username = session_data['username']
    password = session_data['password']
    private_key = session_data.get('private_key', '')

    def generate():
        ssh = None
        try:
            # Connect SSH only (no unused SFTP subsystem channel)
            ssh = connect_ssh_client(hostname, port, username, password, private_key=private_key)
            
            # Stream stats every 3 seconds
            while True:
                stats = get_system_stats(ssh)
                yield f"data: {json.dumps(stats)}\n\n"
                time.sleep(3)
                
        except GeneratorExit:
            # Client disconnected
            if ssh:
                ssh.close()
        except Exception as e:
            yield f"data: {json.dumps({'cpu': 0, 'ram': 0, 'disk': 0, 'network': {'rx': 0, 'tx': 0}})}\n\n"
        finally:
            if ssh:
                ssh.close()

    return Response(stream_with_context(generate()), content_type='text/event-stream')


# --- Users API (Admin Only - Least Privilege) ---

@app.route('/api/users', methods=['GET'])
@role_required('admin')
def api_get_users():
    return jsonify(settings.get_users(safe=True))


@app.route('/api/users', methods=['POST'])
@role_required('admin')
def api_create_user():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    role = data.get('role', 'operator')
    allowed_groups = data.get('allowed_groups', [])
    try:
        user = settings.create_user(
            username=username,
            password=password,
            role=role,
            allowed_groups=allowed_groups
        )
        log_audit_event(
            event_type='USER_CREATED',
            status='SUCCESS',
            severity='NOTICE',
            target={'resource': f"user:{user['username']}", 'user_id': user['id']},
            details={'role': user['role'], 'allowed_groups': user['allowed_groups']}
        )
        return jsonify(user), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/users/<user_id>', methods=['PUT'])
@role_required('admin')
def api_update_user(user_id):
    data = request.get_json(silent=True) or {}
    try:
        result = settings.update_user(
            user_id,
            username=data.get('username'),
            password=data.get('password'),
            role=data.get('role'),
            allowed_groups=data.get('allowed_groups')
        )
        if result is None:
            return jsonify({'error': 'User not found'}), 404
        log_audit_event(
            event_type='USER_UPDATED',
            status='SUCCESS',
            severity='NOTICE',
            target={'resource': f"user:{result['username']}", 'user_id': user_id},
            details={
                'role': result['role'],
                'allowed_groups': result['allowed_groups'],
                'password_changed': bool(data.get('password'))
            }
        )
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/users/<user_id>', methods=['DELETE'])
@role_required('admin')
def api_delete_user(user_id):
    current = get_current_user()
    if current and current['id'] == user_id:
        return jsonify({'error': 'Cannot delete your own active account.'}), 400
    target_user = settings.get_user_by_id(user_id, safe=True)
    try:
        ok = settings.delete_user(user_id)
        if not ok:
            return jsonify({'error': 'User not found'}), 404
        log_audit_event(
            event_type='USER_DELETED',
            status='SUCCESS',
            severity='WARNING',
            target={'resource': f"user:{target_user['username'] if target_user else user_id}", 'user_id': user_id}
        )
        return jsonify({'success': True})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


# --- Audit Log API (Admin Only) ---

@app.route('/api/audit', methods=['GET'])
@role_required('admin')
def api_get_audit():
    limit = request.args.get('limit', 200)
    event_type = request.args.get('event_type') or None
    status = request.args.get('status') or None
    events = get_audit_events(limit=limit, event_type=event_type, status=status)
    chain = verify_audit_chain()
    return jsonify({
        'events': events,
        'chain': chain
    })


@app.route('/api/audit/export', methods=['GET'])
@role_required('admin')
def api_export_audit():
    log_audit_event(
        event_type='AUDIT_LOG_EXPORTED',
        status='SUCCESS',
        severity='NOTICE'
    )
    if not os.path.exists(AUDIT_LOG_FILE):
        return Response('', mimetype='application/x-ndjson', headers={
            'Content-Disposition': 'attachment; filename=amiko_audit.jsonl'
        })
    with open(AUDIT_LOG_FILE, 'r', encoding='utf-8') as f:
        content = f.read()
    return Response(content, mimetype='application/x-ndjson', headers={
        'Content-Disposition': 'attachment; filename=amiko_audit.jsonl'
    })


@app.route('/api/known-hosts', methods=['GET'])
@role_required('admin')
def api_get_known_hosts():
    return jsonify(get_known_hosts_list())


@app.route('/api/known-hosts', methods=['DELETE'])
@role_required('admin')
def api_delete_known_host():
    data = request.get_json(silent=True) or {}
    host_entry = (data.get('host') or '').strip()
    if not host_entry:
        return jsonify({'error': 'host is required'}), 400
    ok = remove_known_host(host_entry)
    if not ok:
        return jsonify({'error': 'Host entry not found'}), 404
    log_audit_event(
        event_type='SSH_HOST_KEY_REVOKED',
        status='SUCCESS',
        severity='WARNING',
        target={'hostname': host_entry}
    )
    return jsonify({'success': True})


# --- Settings API ---

@app.route('/api/groups', methods=['GET'])
def api_get_groups():
    return jsonify(settings.get_groups_for_user(get_current_user()))


@app.route('/api/groups', methods=['POST'])
@role_required('admin')
def api_create_group():
    data = request.get_json()
    name = data.get('name', '').strip()
    icon = data.get('icon', '📁')
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    group = settings.create_group(name, icon)
    log_audit_event(
        event_type='GROUP_CREATED',
        status='SUCCESS',
        severity='INFO',
        target={'resource': f"group:{group['name']}", 'group_id': group['id']}
    )
    return jsonify(group), 201


@app.route('/api/groups/<group_id>', methods=['PUT'])
@role_required('admin')
def api_update_group(group_id):
    data = request.get_json()
    result = settings.update_group(
        group_id,
        name=data.get('name'),
        icon=data.get('icon'),
        order=data.get('order')
    )
    if result is None:
        return jsonify({'error': 'Group not found'}), 404
    log_audit_event(
        event_type='GROUP_UPDATED',
        status='SUCCESS',
        severity='INFO',
        target={'resource': f"group:{result['name']}", 'group_id': group_id}
    )
    return jsonify(result)


@app.route('/api/groups/<group_id>', methods=['DELETE'])
@role_required('admin')
def api_delete_group(group_id):
    settings.delete_group(group_id)
    log_audit_event(
        event_type='GROUP_DELETED',
        status='SUCCESS',
        severity='WARNING',
        target={'resource': f"group:{group_id}", 'group_id': group_id}
    )
    return jsonify({'success': True})


@app.route('/api/servers', methods=['GET'])
def api_get_servers():
    group_id = request.args.get('group_id')
    return jsonify(settings.get_servers_for_user(get_current_user(), group_id))


@app.route('/api/servers', methods=['POST'])
@role_required('admin')
def api_create_server():
    data = request.get_json()
    required = ['group_id', 'name', 'hostname']
    if not all(data.get(f) for f in required):
        return jsonify({'error': 'group_id, name, hostname are required'}), 400
    server = settings.create_server(
        group_id=data['group_id'],
        name=data['name'],
        hostname=data['hostname'],
        port=data.get('port', 22),
        username=data.get('username', 'root'),
        password=data.get('password', ''),
        private_key=data.get('private_key', ''),
        notes=data.get('notes', '')
    )
    log_audit_event(
        event_type='SERVER_CREATED',
        status='SUCCESS',
        severity='NOTICE',
        target={'server_id': server['id'], 'hostname': server['hostname'], 'port': server['port']},
        details={'name': server['name'], 'group_id': server['group_id'], 'ssh_username': server['username']}
    )
    return jsonify(server), 201


@app.route('/api/servers/<server_id>', methods=['PUT'])
@role_required('admin')
def api_update_server(server_id):
    data = request.get_json(silent=True) or {}
    result = settings.update_server(server_id, **data)
    if result is None:
        return jsonify({'error': 'Server not found'}), 404

    # Sync any active SSH session using this server_id with the updated vault record
    raw_srv = settings.get_server_by_id(server_id, safe=False)
    if raw_srv:
        for s_data in ssh_sessions.values():
            if s_data.get('server_id') == server_id:
                s_data['hostname'] = raw_srv.get('hostname', s_data.get('hostname'))
                s_data['port'] = raw_srv.get('port', s_data.get('port'))
                s_data['username'] = raw_srv.get('username', s_data.get('username'))
                s_data['password'] = raw_srv.get('password', '')
                s_data['private_key'] = raw_srv.get('private_key', '')
                s_data['server_name'] = raw_srv.get('name', '')

    log_audit_event(
        event_type='SERVER_UPDATED',
        status='SUCCESS',
        severity='NOTICE',
        target={'server_id': server_id, 'hostname': result['hostname'], 'port': result['port']},
        details={'name': result['name'], 'password_updated': bool(data.get('password'))}
    )
    return jsonify(result)


@app.route('/api/servers/<server_id>', methods=['DELETE'])
@role_required('admin')
def api_delete_server(server_id):
    srv = settings.get_server_by_id(server_id, safe=True)
    settings.delete_server(server_id)
    log_audit_event(
        event_type='SERVER_DELETED',
        status='SUCCESS',
        severity='WARNING',
        target={'server_id': server_id, 'hostname': srv['hostname'] if srv else ''},
        details={'name': srv['name'] if srv else server_id}
    )
    return jsonify({'success': True})


@app.route('/api/servers/quick-list', methods=['GET'])
def api_servers_quick_list():
    return jsonify(settings.get_quick_list_for_user(get_current_user()))


@app.route('/api/themes', methods=['GET'])
def api_get_themes():
    status = settings.get_themes_status()
    # Path relative to core directory
    themes_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'css', 'themes')
    available = []
    
    if os.path.exists(themes_dir):
        for f in sorted(os.listdir(themes_dir)):
            if f.startswith('amiko-') and f.endswith('.css'):
                theme_id = f.replace('.css', '')
                available.append({
                    'id': theme_id,
                    'filename': f,
                    'enabled': status['themes_enabled'].get(theme_id, True),
                    'active': status['active_theme'] == theme_id
                })
                
    return jsonify({
        'active_theme': status['active_theme'],
        'themes': available,
        'crt_enabled': settings.get_crt_enabled()
    })


@app.route('/api/themes/active', methods=['PUT'])
def api_set_active_theme():
    data = request.get_json()
    theme = data.get('theme')
    if not theme:
        return jsonify({'error': 'theme is required'}), 400
    if settings.set_active_theme(theme):
        return jsonify({'success': True, 'active_theme': theme})
    return jsonify({'error': 'Theme not found or disabled'}), 400


@app.route('/api/themes/<theme_name>/toggle', methods=['PUT'])
def api_toggle_theme(theme_name):
    result = settings.toggle_theme(theme_name)
    if result is None:
        return jsonify({'error': 'Cannot disable the active theme'}), 400
    return jsonify({'enabled': result})


@app.route('/api/settings/crt', methods=['PUT'])
def api_toggle_crt():
    data = request.get_json()
    enabled = data.get('enabled', True)
    result = settings.set_crt_enabled(enabled)
    return jsonify({'crt_enabled': result})


@app.route('/api/settings/ui', methods=['GET'])
def api_get_ui_prefs():
    prefs = settings.get_ui_prefs()
    user = get_current_user()
    user_modules = filter_modules_for_user(active_modules, user)
    prefs['active_modules'] = [m['id'] for m in user_modules]
    return jsonify(prefs)


@app.route('/api/settings/ui', methods=['PUT'])
def api_set_ui_prefs():
    data = request.get_json() or {}
    result = settings.set_ui_prefs(data)
    return jsonify(result)


# --- Optional Backup & Restore API (Admin Only) ---

@app.route('/api/backup/export', methods=['POST'])
@role_required('admin')
def api_backup_export():
    from modules.notes.backend import _load_all as load_all_notes
    from utils.sftp_helper import KNOWN_HOSTS_FILE

    data = request.get_json(silent=True) or {}
    passphrase = (data.get('passphrase') or '').strip()

    notes = load_all_notes()
    known_hosts_raw = ''
    if os.path.exists(KNOWN_HOSTS_FILE):
        try:
            with open(KNOWN_HOSTS_FILE, 'r', encoding='utf-8') as f:
                known_hosts_raw = f.read()
        except OSError:
            pass

    bundle = settings.export_backup_bundle(
        passphrase=passphrase,
        notes=notes,
        known_hosts_raw=known_hosts_raw
    )
    log_audit_event(
        event_type='BACKUP_EXPORTED',
        status='SUCCESS',
        severity='NOTICE',
        details={'encrypted': bool(passphrase), 'notes_count': len(notes)}
    )
    return jsonify(bundle)


@app.route('/api/backup/import', methods=['POST'])
@role_required('admin')
def api_backup_import():
    from modules.notes.backend import _save_all as save_all_notes
    from utils.sftp_helper import KNOWN_HOSTS_FILE, DATA_DIR

    data = request.get_json(silent=True) or {}
    bundle = data.get('bundle')
    passphrase = (data.get('passphrase') or '').strip()

    try:
        aux = settings.import_backup_bundle(bundle, passphrase=passphrase)
        save_all_notes(aux.get('notes', []))
        kh_raw = aux.get('known_hosts', '')
        if kh_raw:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(KNOWN_HOSTS_FILE, 'w', encoding='utf-8') as f:
                f.write(kh_raw)
            try:
                os.chmod(KNOWN_HOSTS_FILE, 0o600)
            except OSError:
                pass
        log_audit_event(
            event_type='BACKUP_RESTORED',
            status='SUCCESS',
            severity='WARNING',
            details={'encrypted': bool(bundle and bundle.get('encrypted'))}
        )
        return jsonify({'success': True})
    except ValueError as e:
        log_audit_event(
            event_type='BACKUP_RESTORED',
            status='FAILURE',
            severity='WARNING',
            details={'error': str(e)}
        )
        return jsonify({'error': str(e)}), 400


