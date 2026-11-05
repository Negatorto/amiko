import os
import json
import time
from flask import render_template, request, Response, stream_with_context, jsonify, send_from_directory, session, redirect, url_for

from core import app, active_modules
from core.config import settings, ssh_sessions, MODULES_DIR
from core.auth import get_current_user, filter_modules_for_user, role_required, capability_required
from core.plugins import get_module_manifest, verify_static_module_file, inspect_all_modules
from core.client_ip import get_client_ip
from core.firewall import firewall, report_failure, operator_context
from core.notifier import get_notifier
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
    connect_ssh_client,
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

    source_ip = get_client_ip()

    locked, rem_seconds = check_login_lockout(username, source_ip)
    if locked:
        log_audit_event(
            event_type='AUTH_LOCKOUT',
            status='DENIED',
            severity='ALERT',
            actor={'username': username, 'source_ip': source_ip, 'role': 'none'},
            details={'reason': 'Account/IP locked due to repeated failed attempts', 'remaining_seconds': rem_seconds}
        )
        report_failure('amiko-login', f"Login attempt during lockout for user '{username[:40]}'")
        return jsonify({
            'success': False,
            'error': f'Too many failed attempts. Try again in {rem_seconds}s.'
        }), 429

    user = settings.verify_user(username, password)
    if not user:
        report_failure('amiko-login', f"Invalid credentials for user '{username[:40]}'")
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

    # Check Two-Factor Authentication (TOTP) status
    raw_user = settings.get_user_by_id(user['id'], safe=False)
    totp_enabled = bool(raw_user and raw_user.get('totp_enabled'))

    if totp_enabled:
        # Prompt user for their active 6-digit TOTP code
        session['pending_2fa'] = {
            'user_id': user['id'],
            'username': user['username'],
            'setup': False,
            'expires_at': time.time() + 300
        }
        log_audit_event(
            event_type='AUTH_2FA_CHALLENGE',
            status='SUCCESS',
            severity='INFO',
            actor={'user_id': user['id'], 'username': user['username'], 'role': user['role'], 'source_ip': source_ip},
            details={'setup': False}
        )
        return jsonify({
            'success': False,
            'requires_2fa': True,
            'setup_2fa': False,
            'username': user['username']
        })

    # First access or reset: initiate TOTP enrollment with QR code & recovery codes
    setup_data = settings.init_user_totp_setup(user['id'])
    session['pending_2fa'] = {
        'user_id': user['id'],
        'username': user['username'],
        'setup': True,
        'expires_at': time.time() + 600
    }
    log_audit_event(
        event_type='AUTH_2FA_CHALLENGE',
        status='SUCCESS',
        severity='INFO',
        actor={'user_id': user['id'], 'username': user['username'], 'role': user['role'], 'source_ip': source_ip},
        details={'setup': True}
    )
    return jsonify({
        'success': False,
        'requires_2fa': True,
        'setup_2fa': True,
        'username': user['username'],
        'secret': setup_data['secret'],
        'otpauth_uri': setup_data['otpauth_uri'],
        'qr_data_url': setup_data['qr_data_url'],
        'recovery_codes': setup_data['recovery_codes']
    })


@app.route('/api/auth/2fa/verify', methods=['POST'])
def api_auth_2fa_verify():
    pending = session.get('pending_2fa')
    if not pending or time.time() > pending.get('expires_at', 0):
        session.pop('pending_2fa', None)
        return jsonify({
            'success': False,
            'error': '2FA verification window expired. Please sign in again.',
            'expired': True
        }), 401

    data = request.get_json(silent=True) or {}
    code = (data.get('token') or data.get('code') or '').strip()
    if not code:
        return jsonify({'success': False, 'error': 'Verification code is required.'}), 400

    user_id = pending['user_id']
    username = pending['username']
    source_ip = get_client_ip()

    if pending.get('setup'):
        # Onboarding confirmation
        ok = settings.confirm_user_totp(user_id, code)
        if not ok:
            log_audit_event(
                event_type='2FA_SETUP_FAILED',
                status='FAILURE',
                severity='WARNING',
                actor={'user_id': user_id, 'username': username, 'source_ip': source_ip},
                details={'reason': 'Invalid confirmation code during setup'}
            )
            report_failure('amiko-2fa', f"Invalid 2FA enrollment code for user '{username}'")
            return jsonify({'success': False, 'error': 'Invalid code. Check your app and try again.'}), 400

        log_audit_event(
            event_type='2FA_ENROLLED',
            status='SUCCESS',
            severity='NOTICE',
            actor={'user_id': user_id, 'username': username, 'source_ip': source_ip},
            details={'method': 'TOTP'}
        )
    else:
        # Standard login verification
        ok, method = settings.verify_user_totp(user_id, code)
        if not ok:
            log_audit_event(
                event_type='AUTH_2FA_FAILED',
                status='FAILURE',
                severity='WARNING',
                actor={'user_id': user_id, 'username': username, 'source_ip': source_ip},
                details={'reason': 'Invalid 2FA code or recovery code'}
            )
            report_failure('amiko-2fa', f"Invalid 2FA/recovery code for user '{username}'")
            return jsonify({'success': False, 'error': 'Invalid code. Please try again.'}), 401

        if method == 'recovery_code':
            log_audit_event(
                event_type='2FA_RECOVERY_CODE_USED',
                status='SUCCESS',
                severity='NOTICE',
                actor={'user_id': user_id, 'username': username, 'source_ip': source_ip}
            )

    # 2FA succeeded: establish authenticated session
    session.clear()
    session['user_id'] = user_id
    session['last_activity'] = time.time()
    user = settings.get_user_by_id(user_id, safe=True)
    current = get_current_user()

    log_audit_event(
        event_type='AUTH_LOGIN_SUCCESS',
        status='SUCCESS',
        severity='INFO',
        actor={'user_id': user_id, 'username': username, 'role': user['role'], 'source_ip': source_ip},
        details={'2fa_verified': True}
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
    """Serve verified static files (client.js) from module folders after checking manifest & SHA-256 integrity."""
    user = get_current_user()
    if user and module_name in user.get('capabilities', {}).get('blocked_modules', []):
        return jsonify({'error': 'Module access denied for your role'}), 403

    manifest = get_module_manifest(module_name)
    if not manifest:
        return jsonify({'error': 'Module not loaded or failed integrity verification'}), 403

    allowed_roles = (manifest.get('permissions') or {}).get('allowed_roles', [])
    if user and allowed_roles and user.get('role') not in allowed_roles:
        return jsonify({'error': 'Module access denied by permission manifest'}), 403

    ok, err_msg = verify_static_module_file(module_name, filename)
    if not ok:
        return jsonify({'error': err_msg}), 403

    module_dir = os.path.join(MODULES_DIR, module_name)
    return send_from_directory(module_dir, filename)


@app.route('/api/modules/integrity', methods=['GET'])
@role_required('admin')
def api_modules_integrity():
    """Return permission manifests and SHA-256 integrity status for all modules."""
    return jsonify({'status': 'ok', 'modules': inspect_all_modules()})

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
            allowed_groups=allowed_groups,
            can_manage_firewall=bool(data.get('can_manage_firewall'))
        )
        log_audit_event(
            event_type='USER_CREATED',
            status='SUCCESS',
            severity='NOTICE',
            target={'resource': f"user:{user['username']}", 'user_id': user['id']},
            details={'role': user['role'], 'allowed_groups': user['allowed_groups'],
                     'can_manage_firewall': user.get('can_manage_firewall', False)}
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
            allowed_groups=data.get('allowed_groups'),
            can_manage_firewall=data.get('can_manage_firewall')
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
                'can_manage_firewall': result.get('can_manage_firewall', False),
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


@app.route('/api/users/<user_id>/reset-2fa', methods=['POST', 'PUT'])
@role_required('admin')
def api_reset_user_2fa(user_id):
    target_user = settings.get_user_by_id(user_id, safe=True)
    if not target_user:
        return jsonify({'error': 'User not found'}), 404

    ok = settings.reset_user_totp(user_id)
    if not ok:
        return jsonify({'error': 'Failed to reset 2FA for user'}), 400

    log_audit_event(
        event_type='2FA_RESET',
        status='SUCCESS',
        severity='WARNING',
        target={'resource': f"user:{target_user['username']}", 'user_id': user_id},
        details={'initiated_by': 'admin_ui'}
    )
    return jsonify({
        'success': True,
        'message': f"2FA reset successfully for {target_user['username']}."
    })


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


# --- Firewall & Fail2Ban API (Admin or delegated 'can_manage_firewall') ---

def _firewall_call(fn, event_type, target=None, severity='NOTICE', details_fn=None):
    """Run a firewall mutation, mapping validation/lockout errors to HTTP codes and auditing the outcome."""
    try:
        result = fn()
    except KeyError as e:
        return jsonify({'error': str(e).strip("'")}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except PermissionError as e:
        log_audit_event(
            event_type='FIREWALL_LOCKOUT_PREVENTED',
            status='DENIED',
            severity='WARNING',
            target=target or {'resource': 'firewall'},
            details={'attempted': event_type, 'reason': str(e)}
        )
        return jsonify({'error': str(e), 'lockout_prevented': True}), 409
    log_audit_event(
        event_type=event_type,
        status='SUCCESS',
        severity=severity,
        target=target or {'resource': 'firewall'},
        details=details_fn(result) if details_fn else (result if isinstance(result, dict) else {})
    )
    return jsonify(result)


@app.route('/api/firewall', methods=['GET'])
@capability_required('can_manage_firewall')
def api_firewall_state():
    state = firewall.get_state()
    state['client_ip'] = get_client_ip()
    state['can_manage_kernel'] = get_current_user().get('role') == 'admin'
    return jsonify(state)


@app.route('/api/firewall/settings', methods=['PUT'])
@capability_required('can_manage_firewall')
def api_firewall_settings():
    data = request.get_json(silent=True) or {}
    return _firewall_call(lambda: firewall.update_settings(data, operator_context()),
                          'FIREWALL_SETTINGS_UPDATED', severity='WARNING')


@app.route('/api/firewall/rules', methods=['POST'])
@capability_required('can_manage_firewall')
def api_firewall_add_rule():
    data = request.get_json(silent=True) or {}
    return _firewall_call(lambda: firewall.add_rule(data, operator_context()), 'FIREWALL_RULE_CREATED')


@app.route('/api/firewall/rules/<rule_id>', methods=['PUT'])
@capability_required('can_manage_firewall')
def api_firewall_update_rule(rule_id):
    data = request.get_json(silent=True) or {}
    return _firewall_call(lambda: firewall.update_rule(rule_id, data, operator_context()),
                          'FIREWALL_RULE_UPDATED', target={'resource': f'firewall-rule:{rule_id}'})


@app.route('/api/firewall/rules/<rule_id>', methods=['DELETE'])
@capability_required('can_manage_firewall')
def api_firewall_delete_rule(rule_id):
    return _firewall_call(lambda: firewall.delete_rule(rule_id, operator_context()),
                          'FIREWALL_RULE_DELETED', target={'resource': f'firewall-rule:{rule_id}'},
                          severity='WARNING')


@app.route('/api/firewall/rules/<rule_id>/move', methods=['POST'])
@capability_required('can_manage_firewall')
def api_firewall_move_rule(rule_id):
    direction = (request.get_json(silent=True) or {}).get('direction')
    if direction not in ('up', 'down'):
        return jsonify({'error': "direction must be 'up' or 'down'"}), 400
    return _firewall_call(lambda: firewall.move_rule(rule_id, direction, operator_context()),
                          'FIREWALL_RULE_MOVED', target={'resource': f'firewall-rule:{rule_id}'})


@app.route('/api/firewall/services', methods=['POST'])
@capability_required('can_manage_firewall')
def api_firewall_add_service():
    data = request.get_json(silent=True) or {}
    return _firewall_call(lambda: firewall.add_service(data, operator_context()), 'FIREWALL_SERVICE_CREATED')


@app.route('/api/firewall/services/<service_id>', methods=['PUT'])
@capability_required('can_manage_firewall')
def api_firewall_update_service(service_id):
    data = request.get_json(silent=True) or {}
    return _firewall_call(lambda: firewall.update_service(service_id, data, operator_context()),
                          'FIREWALL_SERVICE_UPDATED', target={'resource': f'firewall-service:{service_id}'})


@app.route('/api/firewall/services/<service_id>', methods=['DELETE'])
@capability_required('can_manage_firewall')
def api_firewall_delete_service(service_id):
    return _firewall_call(lambda: firewall.delete_service(service_id, operator_context()),
                          'FIREWALL_SERVICE_DELETED', target={'resource': f'firewall-service:{service_id}'})


@app.route('/api/firewall/fail2ban', methods=['PUT'])
@capability_required('can_manage_firewall')
def api_firewall_fail2ban():
    data = request.get_json(silent=True) or {}
    return _firewall_call(lambda: firewall.update_fail2ban(data), 'FAIL2BAN_SETTINGS_UPDATED', severity='WARNING')


@app.route('/api/firewall/jails/<jail_name>', methods=['PUT'])
@capability_required('can_manage_firewall')
def api_firewall_jail(jail_name):
    data = request.get_json(silent=True) or {}
    return _firewall_call(lambda: firewall.update_jail(jail_name, data), 'FAIL2BAN_JAIL_UPDATED',
                          target={'resource': f'fail2ban-jail:{jail_name}'})


@app.route('/api/firewall/bans', methods=['GET'])
@capability_required('can_manage_firewall')
def api_firewall_bans():
    return jsonify(firewall.list_bans())


@app.route('/api/firewall/bans', methods=['POST'])
@capability_required('can_manage_firewall')
def api_firewall_manual_ban():
    data = request.get_json(silent=True) or {}
    actor = get_current_user()['username']
    try:
        ban = firewall.manual_ban(data.get('ip', ''), data.get('duration', 60), data.get('reason', ''),
                                  actor=actor, operator_ctx=operator_context())
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(ban), 201


@app.route('/api/firewall/bans', methods=['DELETE'])
@capability_required('can_manage_firewall')
def api_firewall_unban():
    ip = ((request.get_json(silent=True) or {}).get('ip') or '').strip()
    if not firewall.unban(ip, actor=get_current_user()['username']):
        return jsonify({'error': f'{ip} is not currently banned'}), 404
    return jsonify({'success': True})


@app.route('/api/firewall/bans/whitelist', methods=['POST'])
@capability_required('can_manage_firewall')
def api_firewall_whitelist_ban():
    ip = ((request.get_json(silent=True) or {}).get('ip') or '').strip()
    return _firewall_call(lambda: {'entry': firewall.whitelist_ip(ip, actor=get_current_user()['username'])},
                          'FAIL2BAN_WHITELIST_ADDED', target={'resource': f'ip:{ip}'})


@app.route('/api/firewall/kernel', methods=['PUT'])
@role_required('admin')
def api_firewall_kernel():
    enabled = bool((request.get_json(silent=True) or {}).get('enabled'))
    return _firewall_call(lambda: firewall.set_kernel_sync(enabled), 'FIREWALL_KERNEL_SYNC_UPDATED',
                          severity='WARNING')


# --- Email Notifications API (Admin Only) ---

@app.route('/api/notifications', methods=['GET'])
@role_required('admin')
def api_get_notifications():
    return jsonify({
        'config': settings.get_smtp_config(safe=True),
        'history': list(get_notifier().history)
    })


@app.route('/api/notifications', methods=['PUT'])
@role_required('admin')
def api_update_notifications():
    data = request.get_json(silent=True) or {}
    old_cfg = settings.get_smtp_config(safe=False)
    try:
        cfg = settings.update_smtp_config(data)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    changed = sorted(k for k in data if k != 'password' and data.get(k) != old_cfg.get(k))
    record = log_audit_event(
        event_type='NOTIFY_SETTINGS_UPDATED',
        status='SUCCESS',
        severity='WARNING',
        target={'resource': 'smtp'},
        details={'enabled': cfg['enabled'], 'recipient': cfg['recipient'], 'changed_fields': changed,
                 'password_changed': bool(data.get('password'))}
    )
    # Alert the previous recipient, so silencing or redirecting notifications cannot go unnoticed
    get_notifier().notify_config_change(old_cfg, record)
    return jsonify(cfg)


@app.route('/api/notifications/test', methods=['POST'])
@role_required('admin')
def api_test_notification():
    ok, error = get_notifier().send_test()
    if not ok:
        return jsonify({'success': False, 'error': f'Test email failed: {error}'}), 502
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
        'crt_enabled': settings.get_crt_enabled(),
        'bg_effect_enabled': settings.get_bg_effect_enabled()
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
    data = request.get_json() or {}
    enabled = data.get('enabled', True)
    result = settings.set_crt_enabled(enabled)
    return jsonify({'crt_enabled': result})


@app.route('/api/settings/bg-effect', methods=['PUT'])
def api_toggle_bg_effect():
    data = request.get_json() or {}
    enabled = data.get('enabled', True)
    result = settings.set_bg_effect_enabled(enabled)
    return jsonify({'bg_effect_enabled': result})


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


# --- Vault Key Protection & OS Keyring API ---

@app.route('/api/vault/status', methods=['GET'])
def api_vault_status():
    """
    Publicly returns whether the vault is sealed (so /login can display the Unseal screen).
    When authenticated as an admin, returns full key protection and OS Keyring telemetry.
    """
    user = get_current_user() if not settings.is_sealed else None
    status = settings.get_vault_status()
    if user and user.get('role') == 'admin':
        return jsonify(status)
    return jsonify({
        'sealed': status['sealed'],
        'mode': status['mode'],
        'has_recovery_passphrase': status['has_recovery_passphrase']
    })


@app.route('/api/vault/unseal', methods=['POST'])
def api_vault_unseal():
    """Unseal the encrypted vault in RAM using the Master Passphrase."""
    if not settings.is_sealed:
        return jsonify({'success': True, 'sealed': False})

    source_ip = get_client_ip()
    lock_key = "__vault_unseal__"
    locked, rem_seconds = check_login_lockout(lock_key, source_ip)
    if locked:
        log_audit_event(
            event_type='VAULT_UNSEAL_LOCKOUT',
            status='DENIED',
            severity='ALERT',
            actor={'username': 'vault-unseal', 'source_ip': source_ip, 'role': 'none'},
            details={'reason': 'Vault unseal locked due to repeated failed attempts', 'remaining_seconds': rem_seconds}
        )
        report_failure('amiko-login', 'Vault unseal attempt during lockout')
        return jsonify({
            'success': False,
            'error': f'Too many failed unseal attempts. Try again in {rem_seconds}s.'
        }), 429

    data = request.get_json(silent=True) or {}
    passphrase = (data.get('passphrase') or '').strip()
    if not passphrase:
        return jsonify({'success': False, 'error': 'Master Passphrase is required.'}), 400

    try:
        settings.unseal(passphrase)
    except ValueError as e:
        report_failure('amiko-login', 'Invalid Master Passphrase during vault unseal')
        just_locked, lock_rem, fail_count = record_login_attempt(lock_key, source_ip, success=False)
        if just_locked:
            log_audit_event(
                event_type='VAULT_UNSEAL_LOCKOUT',
                status='DENIED',
                severity='ALERT',
                actor={'username': 'vault-unseal', 'source_ip': source_ip, 'role': 'none'},
                details={'failed_attempts': fail_count, 'lockout_seconds': lock_rem}
            )
            return jsonify({
                'success': False,
                'error': f'Too many failed unseal attempts ({fail_count}). Locked for {lock_rem}s.'
            }), 429

        log_audit_event(
            event_type='VAULT_UNSEAL_FAILED',
            status='FAILURE',
            severity='WARNING',
            actor={'username': 'vault-unseal', 'source_ip': source_ip, 'role': 'none'},
            details={'failed_attempts': fail_count, 'error': str(e)}
        )
        return jsonify({'success': False, 'error': 'Invalid Master Passphrase.'}), 401

    record_login_attempt(lock_key, source_ip, success=True)
    status = settings.get_vault_status()
    log_audit_event(
        event_type='VAULT_UNSEALED',
        status='SUCCESS',
        severity='NOTICE',
        actor={'username': 'vault-unseal', 'source_ip': source_ip, 'role': 'admin'},
        details={'mode': status['mode'], 'key_id': status['key_id']}
    )
    return jsonify({'success': True, 'sealed': False, 'key_id': status['key_id']})


@app.route('/api/vault/mode', methods=['POST'])
@role_required('admin')
def api_vault_set_mode():
    """Switch vault key protection mode between local_file, passphrase, and os_keyring."""
    data = request.get_json(silent=True) or {}
    mode = (data.get('mode') or '').strip()
    passphrase = (data.get('passphrase') or '').strip()
    current_passphrase = (data.get('current_passphrase') or '').strip()

    prev_mode = settings.get_vault_status().get('mode')
    try:
        status = settings.set_vault_mode(
            mode=mode,
            passphrase=passphrase,
            current_passphrase=current_passphrase
        )
        log_audit_event(
            event_type='VAULT_MODE_CHANGED',
            status='SUCCESS',
            severity='WARNING',
            details={
                'previous_mode': prev_mode,
                'new_mode': status['mode'],
                'key_file_present': status['key_file_present'],
                'keyring_backend': status.get('configured_keyring_backend'),
                'key_id': status['key_id']
            }
        )
        return jsonify({'success': True, 'vault': status})
    except (ValueError, RuntimeError) as e:
        log_audit_event(
            event_type='VAULT_MODE_CHANGED',
            status='FAILURE',
            severity='WARNING',
            details={'requested_mode': mode, 'error': str(e)}
        )
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/vault/rotate', methods=['POST'])
@role_required('admin')
def api_vault_rotate_key():
    """Rotate the 256-bit Fernet DEK and atomically re-encrypt settings, notes, and firewall stores."""
    data = request.get_json(silent=True) or {}
    passphrase = (data.get('passphrase') or '').strip()
    old_key_id = settings.get_vault_status().get('key_id')

    try:
        status = settings.rotate_vault_key(passphrase=passphrase)
        log_audit_event(
            event_type='VAULT_KEY_ROTATED',
            status='SUCCESS',
            severity='WARNING',
            details={
                'old_key_id': old_key_id,
                'new_key_id': status['key_id'],
                'mode': status['mode']
            }
        )
        return jsonify({'success': True, 'vault': status})
    except (ValueError, RuntimeError) as e:
        log_audit_event(
            event_type='VAULT_KEY_ROTATED',
            status='FAILURE',
            severity='WARNING',
            details={'error': str(e)}
        )
        return jsonify({'success': False, 'error': str(e)}), 400


@app.route('/api/vault/seal', methods=['POST'])
@role_required('admin')
def api_vault_seal():
    """Wipe the active DEK from RAM and lock the vault immediately."""
    user = get_current_user()
    try:
        settings.seal()
        # Disconnect active SSH sessions and clear current session
        for sid, s_info in list(ssh_sessions.items()):
            try:
                if s_info.get('ssh'):
                    s_info['ssh'].close()
            except Exception:
                pass
        ssh_sessions.clear()
        log_audit_event(
            event_type='VAULT_SEALED',
            status='SUCCESS',
            severity='WARNING',
            actor={
                'user_id': user['id'] if user else '',
                'username': user['username'] if user else 'admin',
                'role': 'admin',
                'source_ip': get_client_ip()
            },
            details={'reason': 'Manual seal triggered by administrator'}
        )
        session.clear()
        return jsonify({'success': True, 'sealed': True})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400



