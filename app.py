from gevent import monkey
monkey.patch_all()

import os
import json
import time
import threading
from datetime import datetime
from flask import Flask, render_template, request, Response, stream_with_context, abort, jsonify, send_from_directory, Blueprint
from flask_socketio import SocketIO, emit, disconnect
import paramiko
import importlib.util
from markupsafe import Markup

# Import utility modules
from utils.sftp_helper import (
    connect_sftp,
    connect_ftp,
    execute_ssh_command,
    get_system_stats
)

from utils.settings_manager import SettingsManager

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!' # Change this in production
socketio = SocketIO(app, cors_allowed_origins='*')

# Settings Manager (encrypted storage)
settings = SettingsManager()

# Store active SSH sessions
# Structure: {session_id: {'ssh': client, 'sftp': sftp_client, 'shell': channel, 'thread': thread_obj}}
ssh_sessions = {}

# Plugin System Loader
def load_modules(app):
    modules_dir = os.path.join(os.path.dirname(__file__), 'modules')
    loaded_modules = []
    
    if not os.path.exists(modules_dir):
        os.makedirs(modules_dir)
        return loaded_modules
        
    for mod_name in os.listdir(modules_dir):
        mod_path = os.path.join(modules_dir, mod_name)
        if not os.path.isdir(mod_path):
            continue
            
        json_path = os.path.join(mod_path, 'module.json')
        if not os.path.exists(json_path):
            continue
            
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                mod_data = json.load(f)
                
            if not mod_data.get('enabled', True):
                continue
                
            # Check if icon is an SVG and mark it safe for HTML rendering
            if 'icon' in mod_data and mod_data['icon'].startswith('<svg'):
                mod_data['icon'] = Markup(mod_data['icon'])

            template_html = ""
            tpl_path = os.path.join(mod_path, 'template.html')
            if os.path.exists(tpl_path):
                with open(tpl_path, 'r', encoding='utf-8') as f:
                    template_html = f.read()
                    
            mod_data['template_html'] = template_html
            mod_data['id'] = mod_name
            
            # Check for backend blueprint
            backend_path = os.path.join(mod_path, 'backend.py')
            if os.path.exists(backend_path):
                spec = importlib.util.spec_from_file_location(f"module_{mod_name}", backend_path)
                mod_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod_module)
                
                # Assume blueprint is named 'blueprint' inside backend.py
                if hasattr(mod_module, 'blueprint'):
                    app.register_blueprint(mod_module.blueprint)
                    print(f"[AMIKO Plugin System] Registered Backend Blueprint for: {mod_name}")
                    
            loaded_modules.append(mod_data)
            print(f"[AMIKO Plugin System] Successfully loaded module UI: {mod_name}")
            
        except Exception as e:
            print(f"[AMIKO Plugin System] Error loading module {mod_name}: {e}")
            
    # Sort modules by index, fallback to 999 if not specified
    loaded_modules.sort(key=lambda x: x.get('index', 999))
            
    return loaded_modules
    
# Load modules on startup
active_modules = load_modules(app)

@app.route('/static_modules/<module_name>/<path:filename>')
def serve_module_static(module_name, filename):
    """Serve static files (like client.js) from module folders"""
    module_dir = os.path.join(os.path.dirname(__file__), 'modules', module_name)
    return send_from_directory(module_dir, filename)

@app.route('/')
def index():
    """Main page with 3 monitors"""
    return render_template('index.html', modules=active_modules)

@app.route('/ssh-connect', methods=['POST'])
def ssh_connect():
    """
    Monitor 1: Establish SSH connection
    """
    data = request.get_json()
    hostname = data.get('hostname')
    port = int(data.get('port', 22))
    username = data.get('username')
    password = data.get('password')

    if not all([hostname, username, password]):
        return jsonify({'success': False, 'error': 'Missing credentials'})

    try:
        # Connect using utility function
        ssh, _ = connect_sftp(hostname, port, username, password)
        
        # Determine initial CWD
        stdin, stdout, stderr = ssh.exec_command("pwd")
        initial_cwd = stdout.read().decode('utf-8').strip()
        
        # Generate session ID
        session_id = f"{hostname}_{username}_{int(time.time())}"
        
        # Store SSH connection
        ssh_sessions[session_id] = {
            'ssh': ssh,
            'hostname': hostname,
            'port': port,
            'username': username,
            'password': password,
            'cwd': initial_cwd
        }
        
        return jsonify({'success': True, 'session_id': session_id})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/ssh-command', methods=['POST'])
def ssh_command():
    """
    Monitor 1: Execute SSH command
    """
    data = request.get_json()
    session_id = data.get('session_id')
    command = data.get('command')
    
    # Optional credentials for auto-reconnection
    hostname = data.get('hostname')
    port = data.get('port')
    username = data.get('username')
    password = data.get('password')

    if not command:
        return jsonify({'success': False, 'error': 'Missing command'})

    ssh = None
    session_data = None

    # Try to reuse existing session
    if session_id and session_id in ssh_sessions:
        session_data = ssh_sessions[session_id]
        ssh = session_data['ssh']
    
    # If no session or invalid, try to reconnect if credentials provided
    if not ssh and all([hostname, port, username, password]):
        try:
            # Reconnect
            port = int(port)
            ssh, sftp = connect_sftp(hostname, port, username, password)
            
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
    hostname = request.args.get('hostname')
    port = int(request.args.get('port', 22))
    username = request.args.get('username')
    password = request.args.get('password')

    if not all([hostname, username, password]):
        return "Missing arguments", 400

    def generate():
        ssh = None
        try:
            # Connect
            ssh, _ = connect_sftp(hostname, port, username, password)
            
            # Stream stats every 2 seconds
            while True:
                stats = get_system_stats(ssh)
                yield f"data: {json.dumps(stats)}\n\n"
                time.sleep(2)
                
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


# --- Settings API ---

@app.route('/api/groups', methods=['GET'])
def api_get_groups():
    return jsonify(settings.get_groups())


@app.route('/api/groups', methods=['POST'])
def api_create_group():
    data = request.get_json()
    name = data.get('name', '').strip()
    icon = data.get('icon', '📁')
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    group = settings.create_group(name, icon)
    return jsonify(group), 201


@app.route('/api/groups/<group_id>', methods=['PUT'])
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
    return jsonify(result)


@app.route('/api/groups/<group_id>', methods=['DELETE'])
def api_delete_group(group_id):
    settings.delete_group(group_id)
    return jsonify({'success': True})


@app.route('/api/servers', methods=['GET'])
def api_get_servers():
    group_id = request.args.get('group_id')
    return jsonify(settings.get_servers(group_id))


@app.route('/api/servers', methods=['POST'])
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
        notes=data.get('notes', '')
    )
    return jsonify(server), 201


@app.route('/api/servers/<server_id>', methods=['PUT'])
def api_update_server(server_id):
    data = request.get_json()
    result = settings.update_server(server_id, **data)
    if result is None:
        return jsonify({'error': 'Server not found'}), 404
    return jsonify(result)


@app.route('/api/servers/<server_id>', methods=['DELETE'])
def api_delete_server(server_id):
    settings.delete_server(server_id)
    return jsonify({'success': True})


@app.route('/api/servers/quick-list', methods=['GET'])
def api_servers_quick_list():
    return jsonify(settings.get_quick_list())


@app.route('/api/themes', methods=['GET'])
def api_get_themes():
    status = settings.get_themes_status()
    themes_dir = os.path.join(os.path.dirname(__file__), 'static', 'css', 'themes')
    available = []
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


# --- WebSocket Events for Xterm.js ---

def background_thread(sid, shell):
    """Reads from SSH shell and emits to socket"""
    while True:
        try:
            if shell.recv_ready():
                decoded_data = shell.recv(1024).decode('utf-8', errors='ignore')
                socketio.emit('ssh_output', {'data': decoded_data}, room=sid)
            else:
                socketio.sleep(0.01)
                if shell.closed:
                    break
        except Exception:
            break

@socketio.on('connect_ssh')
def handle_connect_ssh(data):
    hostname = data.get('hostname')
    port = int(data.get('port', 22))
    username = data.get('username')
    password = data.get('password')
    
    try:
        # Create separate SSH client for terminal
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname, port=port, username=username, password=password, timeout=10)
        shell = ssh.invoke_shell(term='xterm', width=80, height=24)
        
        # Store session associated with Socket ID
        ssh_sessions[request.sid] = {
            'ssh': ssh,
            'shell': shell
        }
        
        socketio.start_background_task(target=background_thread, sid=request.sid, shell=shell)
        emit('ssh_status', {'status': 'connected', 'message': f'Connected to {hostname}'})
        
    except Exception as e:
        emit('ssh_status', {'status': 'error', 'message': str(e)})
        disconnect()

@socketio.on('ssh_input')
def handle_ssh_input(data):
    sid = request.sid
    if sid in ssh_sessions:
        shell = ssh_sessions[sid]['shell']
        shell.send(data['data'])

@socketio.on('ssh_resize')
def handle_ssh_resize(data):
    sid = request.sid
    if sid in ssh_sessions:
        shell = ssh_sessions[sid]['shell']
        if not shell.closed:
            shell.resize_pty(width=data['cols'], height=data['rows'])

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in ssh_sessions:
        try:
            ssh_sessions[sid]['shell'].close()
            ssh_sessions[sid]['ssh'].close()
        except:
            pass
        del ssh_sessions[sid]

# --- End WebSocket Events ---

if __name__ == '__main__':
    print("🚀 AMIKO Server in avvio su http://0.0.0.0:5423 ... (Premi CTRL+C per fermare)")
    # Disable reloader to prevent Gevent compatibility issues
    socketio.run(app, debug=True, use_reloader=False, host='0.0.0.0', port=5423)
