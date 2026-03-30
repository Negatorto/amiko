import paramiko
from flask import request
from flask_socketio import emit, disconnect

from core import socketio
from core.config import ssh_sessions

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
        try:
            shell.send(data['data'])
        except:
            pass

@socketio.on('ssh_resize')
def handle_ssh_resize(data):
    sid = request.sid
    if sid in ssh_sessions:
        shell = ssh_sessions[sid]['shell']
        if not shell.closed:
            try:
                shell.resize_pty(width=data['cols'], height=data['rows'])
            except:
                pass

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
