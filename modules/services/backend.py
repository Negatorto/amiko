"""
AMIKO - Services & Containers Module Backend
Self-contained plugin providing:
  - Active system services & listening ports discovery (systemd + ss/netstat + cgroup correlation)
  - Automatic Web Service detection (HTTP/HTTPS) for 1-click browser launch
  - Service lifecycle control (start, stop, restart, reload) and log/status inspection
  - Container management (Docker & Podman auto-detection, port mapping, lifecycle, logs)
"""

import json
import re
import shlex
from flask import Blueprint, request, jsonify
from core.config import settings as _amiko_settings, ssh_sessions as _amiko_ssh_sessions
from core.auth import get_current_user, role_required
from core.audit import log_audit_event
from utils.sftp_helper import connect_ssh_client

blueprint = Blueprint('services', __name__)

# ── Web Service Heuristics ──────────────────────────────────────────────────

# Standard HTTP and HTTPS ports commonly used by web servers, control panels, and web apps
KNOWN_HTTP_PORTS = {
    80, 81, 82, 88, 3000, 3001, 4000, 4040, 4200, 4567, 5000, 5001,
    5173, 5423, 5601, 6080, 7000, 7080, 7474, 8000, 8001, 8008, 8069,
    8080, 8081, 8082, 8086, 8088, 8089, 8090, 8096, 8123, 8200, 8384,
    8500, 8888, 9000, 9001, 9090, 9091, 9093, 9100, 9200, 10000,
    15672, 16686, 19999, 32400
}

KNOWN_HTTPS_PORTS = {
    443, 444, 4443, 5006, 7443, 8006, 8443, 8843, 9443, 10443
}

# Ports that are definitively non-HTTP protocols (never classify as web even if process name is generic)
NON_WEB_PORTS = {
    21, 22, 23, 25, 53, 67, 68, 69, 110, 111, 123, 135, 137, 138, 139,
    143, 161, 162, 389, 445, 465, 514, 587, 631, 636, 873, 993, 995,
    1194, 1433, 1521, 1883, 2049, 2181, 2379, 2380, 3306, 3389, 4369,
    5432, 5672, 5900, 5901, 6379, 6443, 9042, 9300, 11211, 27017, 51820
}

# Process / service / image keywords that strongly indicate an HTTP/HTTPS web interface
WEB_SERVICE_KEYWORDS = (
    'nginx', 'apache', 'httpd', 'lighttpd', 'caddy', 'traefik', 'haproxy',
    'envoy', 'gunicorn', 'uvicorn', 'hypercorn', 'waitress', 'uwsgi', 'passenger',
    'werkzeug', 'flask', 'django', 'fastapi', 'express', 'next', 'nuxt', 'vite',
    'tomcat', 'jetty', 'wildfly', 'grafana', 'prometheus', 'alertmanager',
    'kibana', 'cockpit', 'webmin', 'portainer', 'jenkins', 'gitlab', 'gitea',
    'gog', 'nextcloud', 'owncloud', 'wordpress', 'ghost', 'jellyfin', 'plex',
    'emby', 'homeassistant', 'home-assistant', 'nodered', 'node-red', 'pihole',
    'adguard', 'netdata', 'uptime-kuma', 'vaultwarden', 'bitwarden', 'syncthing',
    'phpmyadmin', 'pgadmin', 'adminer', 'roundcube', 'mailcow', 'sogo',
    'proxmox', 'pveproxy', 'guacamole', 'sonar', 'nexus', 'harbor', 'minio',
    'keycloak', 'authelia', 'authentik', 'zabbix', 'nagios', 'icinga', 'metabase',
    'superset', 'airflow', 'mlflow', 'jupyter', 'code-server', 'ttyd', 'gotty'
)

# Critical services that must not be stopped via the UI to avoid severing the SSH session
PROTECTED_STOP_SERVICES = {
    'ssh', 'sshd', 'ssh.service', 'sshd.service',
    'networking', 'networking.service', 'NetworkManager', 'NetworkManager.service',
    'systemd-networkd', 'systemd-networkd.service'
}

_SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9_@.\-+:]+$')


def _is_web_port(port: int, proto: str = 'tcp', context_hint: str = '', container_internal_port: int = 0) -> dict | None:
    """
    Determine if a listening port is a web service and return its protocol ('http' or 'https').
    Returns {'port': port, 'scheme': 'http'|'https', 'confidence': 'high'|'medium'} or None.
    """
    if proto.lower() != 'tcp':
        return None
    if port in NON_WEB_PORTS:
        return None

    hint_lower = (context_hint or '').lower()

    # 1. Explicit HTTPS ports
    if port in KNOWN_HTTPS_PORTS or container_internal_port in KNOWN_HTTPS_PORTS:
        return {'port': port, 'scheme': 'https', 'confidence': 'high'}

    # Cockpit (9090) and Proxmox (8006) use HTTPS by default when matched
    if port == 9090 and 'cockpit' in hint_lower:
        return {'port': port, 'scheme': 'https', 'confidence': 'high'}

    # 2. Explicit HTTP ports
    if port in KNOWN_HTTP_PORTS or container_internal_port in KNOWN_HTTP_PORTS:
        return {'port': port, 'scheme': 'http', 'confidence': 'high'}

    # 3. Context hint matches a known web server/application on a custom TCP port
    if any(kw in hint_lower for kw in WEB_SERVICE_KEYWORDS):
        scheme = 'https' if ('ssl' in hint_lower or 'tls' in hint_lower or str(port).endswith('443')) else 'http'
        return {'port': port, 'scheme': scheme, 'confidence': 'medium'}

    return None


# ── SSH Connection & Command Execution Helpers ──────────────────────────────

def _resolve_request_connection():
    """Resolve server connection details and check RBAC group access."""
    data = request.get_json(silent=True) or {}
    user = get_current_user()
    resolved = _amiko_settings.resolve_connection_credentials(
        data,
        user=user,
        ssh_sessions=_amiko_ssh_sessions
    )
    if not resolved or not resolved.get('hostname'):
        return None, None, data

    hostname = resolved['hostname']
    port = int(resolved.get('port') or 22)
    if user and not _amiko_settings.can_user_access_server(
        user,
        hostname=hostname,
        port=port,
        server_id=resolved.get('server_id'),
        group_id=resolved.get('group_id')
    ):
        return 'FORBIDDEN', user, data

    return resolved, user, data


def _get_active_or_new_ssh(resolved: dict):
    """
    Reuse the active SSHClient from ssh_sessions if connected and alive,
    or open a fresh SSHClient connection. Returns (ssh_client, should_close_bool).
    """
    session_id = request.get_json(silent=True, force=False)
    if isinstance(session_id, dict):
        sid = session_id.get('session_id')
        if sid and sid in _amiko_ssh_sessions:
            cand = _amiko_ssh_sessions[sid].get('ssh')
            if cand and cand.get_transport() and cand.get_transport().is_active():
                return cand, False

    hostname = resolved['hostname']
    port = int(resolved.get('port') or 22)
    username = resolved['username']

    for s_data in list(_amiko_ssh_sessions.values()):
        if (
            s_data.get('hostname') == hostname
            and int(s_data.get('port', 22)) == port
            and s_data.get('username') == username
        ):
            cand = s_data.get('ssh')
            if cand and cand.get_transport() and cand.get_transport().is_active():
                return cand, False

    ssh = connect_ssh_client(
        hostname=hostname,
        port=port,
        username=username,
        password=resolved.get('password', ''),
        private_key=resolved.get('private_key', ''),
        timeout=10
    )
    return ssh, True


def _run_ssh_command(ssh, command: str, password: str = '', try_sudo: bool = False, timeout: int = 15) -> dict:
    """
    Execute a shell command over SSH.
    If try_sudo is True and the remote user is not root (id -u != 0), attempts sudo -n first,
    then falls back to sudo -S using the SSH password if provided.
    """
    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    code = stdout.channel.recv_exit_status()

    if code != 0 and try_sudo:
        # Retry with sudo -n, or sudo -S if password is available
        sudo_cmd = f"sudo -S -p '' sh -c {shlex.quote(command)}"
        stdin, stdout, stderr = ssh.exec_command(sudo_cmd, timeout=timeout)
        if password:
            try:
                stdin.write(password + '\n')
                stdin.flush()
            except Exception:
                pass
        try:
            stdin.close()
        except Exception:
            pass
        out2 = stdout.read().decode('utf-8', errors='replace')
        err2 = stderr.read().decode('utf-8', errors='replace')
        code2 = stdout.channel.recv_exit_status()
        if code2 == 0 or not out:
            return {'stdout': out2, 'stderr': err2, 'exit_code': code2}

    return {'stdout': out, 'stderr': err, 'exit_code': code}


# ── System Services & Listening Ports Parsing ───────────────────────────────

def _parse_address_port(addr_port: str):
    """Parse '0.0.0.0:80', '[::]:443', '*:8080', '127.0.0.53%lo:53' into (address, port_int)."""
    addr_port = addr_port.strip()
    if ':' not in addr_port:
        return addr_port, None
    addr, port_str = addr_port.rsplit(':', 1)
    if not port_str.isdigit():
        return addr, None
    addr = addr.strip('[]')
    if '%' in addr:
        addr = addr.split('%', 1)[0]
    return addr, int(port_str)


def _parse_services_output(raw_output: str) -> list[dict]:
    """
    Parse combined remote output of:
      1. ===AMIKO_SOCKETS=== (ss -H -tulpn or netstat -tulpn)
      2. ===AMIKO_PID_MAP=== (/proc/<pid>/cgroup service mappings)
      3. ===AMIKO_UNITS===   (systemctl list-units --type=service --all)
    """
    sections = {'sockets': '', 'pid_map': '', 'units': ''}
    current = None
    buf = []

    for line in raw_output.splitlines():
        if line.strip() == '===AMIKO_SOCKETS===':
            current = 'sockets'
            buf = []
        elif line.strip() == '===AMIKO_PID_MAP===':
            if current:
                sections[current] = '\n'.join(buf)
            current = 'pid_map'
            buf = []
        elif line.strip() == '===AMIKO_UNITS===':
            if current:
                sections[current] = '\n'.join(buf)
            current = 'units'
            buf = []
        else:
            if current:
                buf.append(line)
    if current:
        sections[current] = '\n'.join(buf)

    # 1. Build PID -> systemd unit map from /proc/<pid>/cgroup
    pid_to_unit = {}
    pid_map_re = re.compile(r'/proc/(\d+)/cgroup:.*[:/]([^/\s]+\.service)')
    for line in sections['pid_map'].splitlines():
        m = pid_map_re.search(line.strip())
        if m:
            pid_to_unit[int(m.group(1))] = m.group(2)

    # 2. Parse listening sockets from ss -H -tulpn
    unit_ports = {}         # unit_name -> list of port dicts
    standalone_procs = {}   # proc_key -> dict for processes not in a systemd unit
    proc_re = re.compile(r'\("([^"]+)",pid=(\d+)')

    for line in sections['sockets'].splitlines():
        line = line.strip()
        if not line or line.startswith('Netid') or line.startswith('Active') or line.startswith('Proto'):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue

        proto = parts[0].lower()
        if not (proto.startswith('tcp') or proto.startswith('udp')):
            continue
        proto_clean = 'tcp' if proto.startswith('tcp') else 'udp'

        # In ss -H -tulpn: Netid State Recv-Q Send-Q Local_Address:Port Peer_Address:Port Process
        state = parts[1].upper()
        if proto_clean == 'tcp' and state not in ('LISTEN', 'UNCONN'):
            continue

        local_field = parts[4] if state in ('LISTEN', 'UNCONN', 'ESTAB') else parts[3]
        addr, port_num = _parse_address_port(local_field)
        if port_num is None:
            continue

        is_loopback = addr in ('127.0.0.1', '::1', 'localhost') or addr.startswith('127.')
        scope = 'local' if is_loopback else 'public'

        matches = proc_re.findall(line)
        matched_units = set()
        proc_names = set()
        pids = []

        for p_name, pid_str in matches:
            pid_int = int(pid_str)
            pids.append(pid_int)
            proc_names.add(p_name)
            if pid_int in pid_to_unit:
                matched_units.add(pid_to_unit[pid_int])

        port_info = {
            'port': port_num,
            'proto': proto_clean,
            'bind': addr or '*',
            'scope': scope,
            'process': ', '.join(sorted(proc_names)) if proc_names else '',
            'pids': pids
        }

        if matched_units:
            for u in matched_units:
                unit_ports.setdefault(u, []).append(port_info)
        else:
            # Try matching process name to a systemd unit name heuristic
            proc_label = sorted(proc_names)[0] if proc_names else f"port-{port_num}"
            first_pid = pids[0] if pids else 0
            key = f"proc:{proc_label}" if proc_names else f"port:{proto_clean}:{port_num}"
            entry = standalone_procs.setdefault(key, {
                'unit': f"{proc_label} (PID {first_pid})" if first_pid else f"Port :{port_num}",
                'name': proc_label,
                'kind': 'process',
                'pid': first_pid,
                'load_state': 'loaded',
                'active_state': 'active',
                'sub_state': 'listening',
                'description': f"Standalone listening process ({proc_label})" if proc_names else f"Listening socket on {proto_clean.upper()} :{port_num}",
                'ports': []
            })
            entry['ports'].append(port_info)

    # 3. Parse systemd units
    services_by_unit = {}
    for line in sections['units'].splitlines():
        line = line.strip()
        if not line or line.startswith('UNIT '):
            continue
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit_name = parts[0]
        if not unit_name.endswith('.service'):
            continue
        load_state = parts[1]
        active_state = parts[2]
        sub_state = parts[3]
        description = parts[4] if len(parts) > 4 else unit_name

        short_name = unit_name[:-8]  # strip '.service'
        services_by_unit[unit_name] = {
            'unit': unit_name,
            'name': short_name,
            'kind': 'systemd',
            'pid': 0,
            'load_state': load_state,
            'active_state': active_state,
            'sub_state': sub_state,
            'description': description,
            'ports': unit_ports.pop(unit_name, [])
        }

    # Any unit found in cgroup that wasn't in systemctl list-units output
    for leftover_unit, p_list in unit_ports.items():
        short_name = leftover_unit[:-8] if leftover_unit.endswith('.service') else leftover_unit
        services_by_unit[leftover_unit] = {
            'unit': leftover_unit,
            'name': short_name,
            'kind': 'systemd',
            'pid': p_list[0]['pids'][0] if p_list and p_list[0].get('pids') else 0,
            'load_state': 'loaded',
            'active_state': 'active',
            'sub_state': 'running',
            'description': f"System service ({short_name})",
            'ports': p_list
        }

    # Try linking standalone processes to matching systemd services by name if cgroup wasn't readable
    for key in list(standalone_procs.keys()):
        sp = standalone_procs[key]
        pname = sp['name'].lower()
        for u_name, u_data in services_by_unit.items():
            s_name = u_data['name'].lower()
            if pname and (s_name == pname or s_name.startswith(pname + '-') or pname.startswith(s_name)):
                u_data['ports'].extend(sp['ports'])
                del standalone_procs[key]
                break

    all_items = list(services_by_unit.values()) + list(standalone_procs.values())

    # Deduplicate ports and enrich with web detection
    results = []
    for item in all_items:
        dedup = {}
        for p in item['ports']:
            pkey = (p['proto'], p['port'])
            if pkey not in dedup or (dedup[pkey]['scope'] == 'local' and p['scope'] == 'public'):
                dedup[pkey] = p

        sorted_ports = sorted(dedup.values(), key=lambda x: (x['proto'] != 'tcp', x['port']))
        web_links = []
        hint = f"{item['name']} {item['description']}"

        for p in sorted_ports:
            p_hint = f"{hint} {p.get('process', '')}"
            web_info = _is_web_port(p['port'], p['proto'], p_hint)
            if web_info:
                p['is_web'] = True
                p['scheme'] = web_info['scheme']
                web_links.append({
                    'port': p['port'],
                    'scheme': web_info['scheme'],
                    'scope': p['scope'],
                    'confidence': web_info['confidence']
                })
            else:
                p['is_web'] = False
                p['scheme'] = 'http' if p['proto'] == 'tcp' and p['port'] not in NON_WEB_PORTS else None

        item['ports'] = sorted_ports
        item['web_links'] = web_links
        item['is_web'] = len(web_links) > 0
        item['is_listening'] = len(sorted_ports) > 0
        item['is_protected'] = item['unit'] in PROTECTED_STOP_SERVICES or item['name'] in PROTECTED_STOP_SERVICES

        # Filter out noisy systemd oneshot/dead internal services that have no ports and aren't failed
        if (
            item['kind'] == 'systemd'
            and not item['is_listening']
            and item['active_state'] == 'inactive'
            and item['sub_state'] == 'dead'
            and item['name'].startswith(('systemd-', 'plymouth-', 'apt-', 'dpkg-', 'fstrim', 'man-db', 'e2scrub', 'ua-'))
        ):
            continue

        results.append(item)

    # Sort: Web services first, then listening services, then running services, then failed, then alphabetical
    def _sort_key(s):
        state_rank = 0 if s['active_state'] == 'active' else (1 if s['active_state'] == 'failed' else 2)
        return (
            0 if s['is_web'] else 1,
            0 if s['is_listening'] else 1,
            state_rank,
            s['name'].lower()
        )

    results.sort(key=_sort_key)
    return results


# ── Containers Parsing (Docker & Podman) ────────────────────────────────────

_DOCKER_PORT_RE = re.compile(
    r'(?:(?P<host_ip>[0-9a-fA-F.:]+|\*):)?(?P<host_port>\d+)->(?P<container_port>\d+)/(?P<proto>tcp|udp)'
)


def _parse_container_ports(ports_str: str, context_hint: str = '') -> tuple[list[dict], list[dict]]:
    """
    Parse Docker/Podman Ports string (e.g. '0.0.0.0:8080->80/tcp, :::8080->80/tcp, 3306/tcp')
    into (published_ports, web_links).
    """
    if not ports_str:
        return [], []

    seen = {}
    for m in _DOCKER_PORT_RE.finditer(ports_str):
        host_ip = (m.group('host_ip') or '0.0.0.0').strip('[]')
        host_port = int(m.group('host_port'))
        c_port = int(m.group('container_port'))
        proto = m.group('proto').lower()

        is_local = host_ip in ('127.0.0.1', '::1', 'localhost')
        scope = 'local' if is_local else 'public'
        key = (proto, host_port, c_port)

        if key not in seen or (seen[key]['scope'] == 'local' and scope == 'public'):
            web_info = _is_web_port(host_port, proto, context_hint, container_internal_port=c_port)
            seen[key] = {
                'host_port': host_port,
                'container_port': c_port,
                'proto': proto,
                'bind': host_ip,
                'scope': scope,
                'is_web': web_info is not None,
                'scheme': web_info['scheme'] if web_info else ('http' if proto == 'tcp' and host_port not in NON_WEB_PORTS else None)
            }

    ports_list = sorted(seen.values(), key=lambda x: x['host_port'])
    web_links = [
        {
            'port': p['host_port'],
            'container_port': p['container_port'],
            'scheme': p['scheme'],
            'scope': p['scope']
        }
        for p in ports_list if p['is_web']
    ]
    return ports_list, web_links


# ── HTTP Endpoints ──────────────────────────────────────────────────────────

@blueprint.route('/services/overview', methods=['POST'])
def services_overview():
    """Fetch active services, listening network ports, and detected web services."""
    resolved, user, data = _resolve_request_connection()
    if resolved == 'FORBIDDEN':
        return jsonify({'status': 'error', 'message': 'Access to this server is not permitted for your assigned groups.'}), 403
    if not resolved:
        return jsonify({'status': 'error', 'message': 'Missing connection parameters or server not connected.'}), 400

    ssh = None
    should_close = False
    try:
        ssh, should_close = _get_active_or_new_ssh(resolved)
        remote_script = (
            "export LC_ALL=C; "
            "echo '===AMIKO_SOCKETS==='; "
            "(sudo -n ss -H -tulpn 2>/dev/null || ss -H -tulpn 2>/dev/null || netstat -tulpn 2>/dev/null); "
            "echo '===AMIKO_PID_MAP==='; "
            "grep -H -oE '[^/]+\\.service' /proc/[0-9]*/cgroup 2>/dev/null || true; "
            "echo '===AMIKO_UNITS==='; "
            "systemctl list-units --type=service --all --no-pager --no-legend --plain 2>/dev/null || true"
        )
        res = _run_ssh_command(ssh, remote_script, password=resolved.get('password', ''), try_sudo=False, timeout=15)
        services = _parse_services_output(res['stdout'])

        summary = {
            'total': len(services),
            'running': sum(1 for s in services if s['active_state'] == 'active'),
            'listening': sum(1 for s in services if s['is_listening']),
            'web': sum(1 for s in services if s['is_web']),
            'failed': sum(1 for s in services if s['active_state'] == 'failed'),
        }

        return jsonify({
            'status': 'ok',
            'hostname': resolved['hostname'],
            'summary': summary,
            'services': services
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Failed to query services: {str(e)}'}), 500
    finally:
        if should_close and ssh:
            try:
                ssh.close()
            except Exception:
                pass


@blueprint.route('/services/action', methods=['POST'])
@role_required('admin', 'operator')
def service_action():
    """Start, stop, restart, or reload a systemd service (or stop a standalone process)."""
    resolved, user, data = _resolve_request_connection()
    if resolved == 'FORBIDDEN':
        return jsonify({'status': 'error', 'message': 'Access to this server is not permitted for your assigned groups.'}), 403
    if not resolved:
        return jsonify({'status': 'error', 'message': 'Missing connection parameters.'}), 400

    action = (data.get('action') or '').strip().lower()
    unit = (data.get('unit') or '').strip()
    kind = (data.get('kind') or 'systemd').strip().lower()
    pid = int(data.get('pid') or 0)

    if action not in ('start', 'stop', 'restart', 'reload'):
        return jsonify({'status': 'error', 'message': f'Invalid action: {action}'}), 400

    if kind == 'systemd':
        if not unit or not _SAFE_NAME_RE.match(unit):
            return jsonify({'status': 'error', 'message': 'Invalid service unit name.'}), 400
        if action == 'stop' and (unit in PROTECTED_STOP_SERVICES or unit.removesuffix('.service') in PROTECTED_STOP_SERVICES):
            return jsonify({
                'status': 'error',
                'message': f"Safety lock: stopping '{unit}' is blocked to prevent severing your SSH connection. Use Restart if needed."
            }), 409
        cmd = f"systemctl {action} {shlex.quote(unit)}"
    elif kind == 'process':
        if action != 'stop' or pid <= 1:
            return jsonify({'status': 'error', 'message': 'Standalone processes only support the Stop (SIGTERM) action.'}), 400
        cmd = f"kill -15 {pid}"
    else:
        return jsonify({'status': 'error', 'message': 'Unsupported service kind.'}), 400

    ssh = None
    should_close = False
    try:
        ssh, should_close = _get_active_or_new_ssh(resolved)
        res = _run_ssh_command(ssh, cmd, password=resolved.get('password', ''), try_sudo=True, timeout=25)

        if res['exit_code'] != 0:
            err_msg = (res['stderr'] or res['stdout'] or 'Command failed').strip()
            log_audit_event(
                event_type='SERVICE_CONTROL',
                status='FAILURE',
                severity='WARNING',
                target={'hostname': resolved['hostname'], 'service': unit or f'pid:{pid}'},
                details={'action': action, 'error': err_msg[:200]}
            )
            return jsonify({'status': 'error', 'message': f"Failed to {action} {unit}: {err_msg}"}), 500

        log_audit_event(
            event_type='SERVICE_CONTROL',
            status='SUCCESS',
            severity='NOTICE',
            target={'hostname': resolved['hostname'], 'service': unit or f'pid:{pid}'},
            details={'action': action, 'kind': kind}
        )
        return jsonify({
            'status': 'ok',
            'message': f"Service '{unit or pid}' {action}ed successfully."
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if should_close and ssh:
            try:
                ssh.close()
            except Exception:
                pass


@blueprint.route('/services/logs', methods=['POST'])
def service_logs():
    """Retrieve systemctl status and recent journalctl logs for a service unit."""
    resolved, user, data = _resolve_request_connection()
    if resolved == 'FORBIDDEN':
        return jsonify({'status': 'error', 'message': 'Access to this server is not permitted.'}), 403
    if not resolved:
        return jsonify({'status': 'error', 'message': 'Missing connection parameters.'}), 400

    unit = (data.get('unit') or '').strip()
    if not unit or not _SAFE_NAME_RE.match(unit):
        return jsonify({'status': 'error', 'message': 'Invalid service unit name.'}), 400

    ssh = None
    should_close = False
    try:
        ssh, should_close = _get_active_or_new_ssh(resolved)
        q_unit = shlex.quote(unit)
        cmd = (
            f"export LC_ALL=C; "
            f"echo '=== SYSTEMCTL STATUS ==='; "
            f"systemctl status {q_unit} --no-pager -l 2>&1 || true; "
            f"echo ''; echo '=== RECENT JOURNAL LOGS (last 50 lines) ==='; "
            f"(sudo -n journalctl -u {q_unit} -n 50 --no-pager 2>/dev/null || journalctl -u {q_unit} -n 50 --no-pager 2>&1) || true"
        )
        res = _run_ssh_command(ssh, cmd, password=resolved.get('password', ''), try_sudo=False, timeout=15)
        return jsonify({
            'status': 'ok',
            'unit': unit,
            'logs': res['stdout'] or res['stderr'] or 'No logs available.'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if should_close and ssh:
            try:
                ssh.close()
            except Exception:
                pass


# ── Container Routes (Docker / Podman) ──────────────────────────────────────

@blueprint.route('/services/containers', methods=['POST'])
def list_containers():
    """List all Docker or Podman containers with state, ports, and 1-click Web links."""
    resolved, user, data = _resolve_request_connection()
    if resolved == 'FORBIDDEN':
        return jsonify({'status': 'error', 'message': 'Access to this server is not permitted for your assigned groups.'}), 403
    if not resolved:
        return jsonify({'status': 'error', 'message': 'Missing connection parameters or server not connected.'}), 400

    ssh = None
    should_close = False
    try:
        ssh, should_close = _get_active_or_new_ssh(resolved)
        cmd = (
            "export LC_ALL=C; "
            "if command -v docker >/dev/null 2>&1; then "
            "  echo 'RUNTIME:docker'; "
            "  (docker ps -a --no-trunc --format '{{json .}}' 2>/dev/null || sudo -n docker ps -a --no-trunc --format '{{json .}}' 2>/dev/null); "
            "elif command -v podman >/dev/null 2>&1; then "
            "  echo 'RUNTIME:podman'; "
            "  (podman ps -a --no-trunc --format '{{json .}}' 2>/dev/null || sudo -n podman ps -a --no-trunc --format '{{json .}}' 2>/dev/null); "
            "else "
            "  echo 'RUNTIME:none'; "
            "fi"
        )
        res = _run_ssh_command(ssh, cmd, password=resolved.get('password', ''), try_sudo=False, timeout=15)
        lines = [ln.strip() for ln in (res['stdout'] or '').splitlines() if ln.strip()]

        runtime = 'none'
        if lines and lines[0].startswith('RUNTIME:'):
            runtime = lines[0].split(':', 1)[1].strip()
            lines = lines[1:]

        # If docker/podman is installed but required sudo with password (empty output and non-zero exit)
        if runtime in ('docker', 'podman') and not lines and res['exit_code'] != 0 and resolved.get('password'):
            sudo_res = _run_ssh_command(
                ssh,
                f"{runtime} ps -a --no-trunc --format '{{{{json .}}}}'",
                password=resolved.get('password', ''),
                try_sudo=True,
                timeout=15
            )
            if sudo_res['exit_code'] == 0:
                lines = [ln.strip() for ln in (sudo_res['stdout'] or '').splitlines() if ln.strip()]

        containers = []
        for raw_line in lines:
            if not raw_line.startswith('{'):
                continue
            try:
                obj = json.loads(raw_line)
            except Exception:
                continue

            c_id = str(obj.get('ID') or obj.get('Id') or '')[:12]
            names = obj.get('Names') or obj.get('Name') or c_id
            if isinstance(names, list):
                names = ', '.join(str(x) for x in names)
            name_clean = str(names).lstrip('/')

            image = str(obj.get('Image') or '')
            state = str(obj.get('State') or '').lower()
            status_text = str(obj.get('Status') or state)
            ports_raw = obj.get('Ports') or ''
            if isinstance(ports_raw, list):
                # Podman JSON format sometimes returns list of port mappings
                formatted_parts = []
                for pm in ports_raw:
                    if isinstance(pm, dict):
                        hip = pm.get('host_ip') or '0.0.0.0'
                        hp = pm.get('host_port')
                        cp = pm.get('container_port')
                        pr = pm.get('protocol', 'tcp')
                        if hp and cp:
                            formatted_parts.append(f"{hip}:{hp}->{cp}/{pr}")
                ports_raw = ', '.join(formatted_parts)
            else:
                ports_raw = str(ports_raw)

            if not state:
                if status_text.lower().startswith('up'):
                    state = 'running'
                elif status_text.lower().startswith('exited'):
                    state = 'exited'
                else:
                    state = 'unknown'

            ports_list, web_links = _parse_container_ports(ports_raw, context_hint=f"{name_clean} {image}")

            containers.append({
                'id': c_id,
                'name': name_clean,
                'image': image,
                'state': state,
                'status': status_text,
                'ports_raw': ports_raw,
                'ports': ports_list,
                'web_links': web_links if state == 'running' else [],
                'is_web': len(web_links) > 0 and state == 'running'
            })

        containers.sort(key=lambda c: (0 if c['state'] == 'running' else 1, 0 if c['is_web'] else 1, c['name'].lower()))

        summary = {
            'runtime': runtime,
            'total': len(containers),
            'running': sum(1 for c in containers if c['state'] == 'running'),
            'stopped': sum(1 for c in containers if c['state'] != 'running'),
            'web': sum(1 for c in containers if c['is_web'])
        }

        return jsonify({
            'status': 'ok',
            'hostname': resolved['hostname'],
            'runtime': runtime,
            'summary': summary,
            'containers': containers
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Failed to query containers: {str(e)}'}), 500
    finally:
        if should_close and ssh:
            try:
                ssh.close()
            except Exception:
                pass


@blueprint.route('/services/container-action', methods=['POST'])
@role_required('admin', 'operator')
def container_action():
    """Start, stop, or restart a Docker/Podman container."""
    resolved, user, data = _resolve_request_connection()
    if resolved == 'FORBIDDEN':
        return jsonify({'status': 'error', 'message': 'Access to this server is not permitted for your assigned groups.'}), 403
    if not resolved:
        return jsonify({'status': 'error', 'message': 'Missing connection parameters.'}), 400

    action = (data.get('action') or '').strip().lower()
    container_id = (data.get('container_id') or '').strip()
    runtime = (data.get('runtime') or 'docker').strip().lower()

    if runtime not in ('docker', 'podman'):
        runtime = 'docker'
    if action not in ('start', 'stop', 'restart', 'pause', 'unpause'):
        return jsonify({'status': 'error', 'message': f'Invalid container action: {action}'}), 400
    if not container_id or not _SAFE_NAME_RE.match(container_id):
        return jsonify({'status': 'error', 'message': 'Invalid container identifier.'}), 400

    ssh = None
    should_close = False
    try:
        ssh, should_close = _get_active_or_new_ssh(resolved)
        cmd = f"{runtime} {action} {shlex.quote(container_id)}"
        res = _run_ssh_command(ssh, cmd, password=resolved.get('password', ''), try_sudo=True, timeout=30)

        if res['exit_code'] != 0:
            err_msg = (res['stderr'] or res['stdout'] or 'Container command failed').strip()
            log_audit_event(
                event_type='CONTAINER_CONTROL',
                status='FAILURE',
                severity='WARNING',
                target={'hostname': resolved['hostname'], 'container': container_id},
                details={'runtime': runtime, 'action': action, 'error': err_msg[:200]}
            )
            return jsonify({'status': 'error', 'message': f"Failed to {action} container '{container_id}': {err_msg}"}), 500

        log_audit_event(
            event_type='CONTAINER_CONTROL',
            status='SUCCESS',
            severity='NOTICE',
            target={'hostname': resolved['hostname'], 'container': container_id},
            details={'runtime': runtime, 'action': action}
        )
        return jsonify({
            'status': 'ok',
            'message': f"Container '{container_id}' {action}ed successfully."
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if should_close and ssh:
            try:
                ssh.close()
            except Exception:
                pass


@blueprint.route('/services/container-logs', methods=['POST'])
def container_logs():
    """Fetch recent logs from a Docker or Podman container."""
    resolved, user, data = _resolve_request_connection()
    if resolved == 'FORBIDDEN':
        return jsonify({'status': 'error', 'message': 'Access to this server is not permitted.'}), 403
    if not resolved:
        return jsonify({'status': 'error', 'message': 'Missing connection parameters.'}), 400

    container_id = (data.get('container_id') or '').strip()
    runtime = (data.get('runtime') or 'docker').strip().lower()
    if runtime not in ('docker', 'podman'):
        runtime = 'docker'
    if not container_id or not _SAFE_NAME_RE.match(container_id):
        return jsonify({'status': 'error', 'message': 'Invalid container identifier.'}), 400

    ssh = None
    should_close = False
    try:
        ssh, should_close = _get_active_or_new_ssh(resolved)
        cmd = f"{runtime} logs --tail 80 --timestamps {shlex.quote(container_id)} 2>&1"
        res = _run_ssh_command(ssh, cmd, password=resolved.get('password', ''), try_sudo=True, timeout=15)
        return jsonify({
            'status': 'ok',
            'container_id': container_id,
            'logs': res['stdout'] or res['stderr'] or 'No container output.'
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if should_close and ssh:
            try:
                ssh.close()
            except Exception:
                pass

