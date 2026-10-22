/*
 * AMIKO - Global Connection Management
 * Handles unified login system for all services
 */

// Global connection state
window.globalConnection = {
    connected: false,
    hostname: null,
    port: null,
    username: null,
    password: null,
    sessionId: null,
    statsStream: null,
    serverId: null,
    groupId: null
};

// Initialize global connection handlers
document.addEventListener('DOMContentLoaded', function () {
    const globalLoginForm = document.getElementById('globalLoginForm');
    const globalConnectBtn = document.getElementById('globalConnectBtn');
    const globalDisconnectBtn = document.getElementById('globalDisconnectBtn');
    const connectionForm = document.getElementById('connectionForm');
    const connectionStatus = document.getElementById('connectionStatus');
    const resourceMonitorHeader = document.getElementById('resourceMonitorHeader');

    // Handle global login
    globalLoginForm.addEventListener('submit', async function (e) {
        e.preventDefault();

        const hostname = document.getElementById('global_hostname').value;
        const port = document.getElementById('global_port').value;
        const username = document.getElementById('global_username').value;
        const password = document.getElementById('global_password').value;

        // Extract server/group IDs from the dropdown selection (or Settings "Use" action)
        let selectedServerId = window.globalConnection.serverId || null;
        let selectedGroupId = window.globalConnection.groupId || null;
        const savedSelect = document.getElementById('saved_servers');
        if (savedSelect && savedSelect.value && savedSelect.value !== 'new') {
            try {
                const sel = JSON.parse(savedSelect.value);
                selectedServerId = sel.id || selectedServerId;
                selectedGroupId  = sel.group_id || selectedGroupId;
            } catch { /* manual entry — no IDs */ }
        }

        globalConnectBtn.disabled = true;
        globalConnectBtn.textContent = 'CONNECTING...';

        try {
            // Test connection via SSH endpoint (resolves password server-side when server_id is used)
            const response = await fetch('/ssh-connect', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    server_id: selectedServerId,
                    group_id: selectedGroupId,
                    hostname,
                    port,
                    username,
                    password
                })
            });

            const result = await response.json();

            if (result.success) {
                window.globalConnection.connected = true;
                window.globalConnection.hostname = result.hostname || hostname;
                window.globalConnection.port = result.port || port;
                window.globalConnection.username = result.username || username;
                window.globalConnection.password = null;
                window.globalConnection.sessionId = result.session_id;
                window.globalConnection.serverId = result.server_id || selectedServerId;
                window.globalConnection.groupId  = result.group_id || selectedGroupId;

                updateConnectionUI(true, window.globalConnection.hostname, window.globalConnection.port, window.globalConnection.username);

                sessionStorage.setItem('globalConnection', JSON.stringify({
                    hostname: window.globalConnection.hostname,
                    port: window.globalConnection.port,
                    username: window.globalConnection.username,
                    sessionId: window.globalConnection.sessionId,
                    serverId: window.globalConnection.serverId,
                    groupId:  window.globalConnection.groupId
                }));

                startStatsMonitoring();

                window.dispatchEvent(new CustomEvent('amikoGlobalConnect', {
                    detail: {
                        hostname: window.globalConnection.hostname,
                        port: window.globalConnection.port,
                        username: window.globalConnection.username,
                        serverId: window.globalConnection.serverId,
                        sessionId: window.globalConnection.sessionId
                    }
                }));
                console.log('Global connection established');
            } else {
                if (window.AmikoAlert) window.AmikoAlert('Connection Error', result.error);
                else alert('Connection error: ' + result.error);
                globalConnectBtn.disabled = false;
                globalConnectBtn.textContent = 'CONNECT';
            }
        } catch (error) {
            console.error('Connection error:', error);
            if (window.AmikoAlert) window.AmikoAlert('Error', 'Error during connection');
            else alert('Error during connection');
            globalConnectBtn.disabled = false;
            globalConnectBtn.textContent = 'CONNECT';
        }
    });

    // Handle disconnect
    globalDisconnectBtn.addEventListener('click', function () {
        disconnectGlobal();
    });

    // Check if there's a saved connection metadata in sessionStorage
    const savedConnection = sessionStorage.getItem('globalConnection');
    if (savedConnection) {
        const conn = JSON.parse(savedConnection);
        document.getElementById('global_hostname').value = conn.hostname || '';
        document.getElementById('global_port').value = conn.port || 22;
        document.getElementById('global_username').value = conn.username || '';
        const pwInput = document.getElementById('global_password');
        if (pwInput) {
            pwInput.value = '';
            if (conn.serverId) {
                pwInput.placeholder = 'Stored in Vault 🔒';
                pwInput.required = false;
            }
        }
        // Restore server/group IDs if present
        if (conn.serverId) window.globalConnection.serverId = conn.serverId;
        if (conn.groupId)  window.globalConnection.groupId  = conn.groupId;
    }

    // Load saved servers into dropdown
    loadSavedServers();
});

// Update connection UI
function updateConnectionUI(connected, hostname, port, username) {
    const connectionForm = document.getElementById('connectionForm');
    const connectionStatus = document.getElementById('connectionStatus');
    const globalConnectBtn = document.getElementById('globalConnectBtn');
    const globalDisconnectBtn = document.getElementById('globalDisconnectBtn');
    const resourceMonitorHeader = document.getElementById('resourceMonitorHeader');
    const statusDot = document.querySelector('.status-dot');
    const statusText = document.getElementById('statusText');
    const connectionInfo = document.getElementById('connectionInfo');

    if (connected) {
        connectionForm.classList.add('hidden');
        connectionStatus.classList.remove('hidden');
        // Show the vitals block that matches the current display mode
        const mode = localStorage.getItem('amiko_vitals_mode') || 'bars';
        if (mode === 'text') {
            const textEl = document.getElementById('resourceMonitorText');
            if (textEl) textEl.classList.remove('hidden');
        } else {
            resourceMonitorHeader.classList.remove('hidden');
        }
        globalConnectBtn.classList.add('hidden');
        globalDisconnectBtn.classList.remove('hidden');

        statusDot.classList.add('connected');
        statusText.textContent = 'CONNECTED';
        connectionInfo.textContent = `${username}@${hostname}:${port}`;
    } else {
        connectionForm.classList.remove('hidden');
        connectionStatus.classList.add('hidden');
        resourceMonitorHeader.classList.add('hidden');
        const textEl = document.getElementById('resourceMonitorText');
        if (textEl) textEl.classList.add('hidden');
        globalConnectBtn.classList.remove('hidden');
        globalDisconnectBtn.classList.add('hidden');
        globalConnectBtn.disabled = false;
        globalConnectBtn.textContent = 'CONNECT';

        statusDot.classList.remove('connected');
        statusText.textContent = 'DISCONNECTED';
        connectionInfo.textContent = '';
    }
}

// Start stats monitoring
function startStatsMonitoring() {
    if (window.globalConnection.statsStream) {
        window.globalConnection.statsStream.close();
    }

    const params = new URLSearchParams({
        session_id: window.globalConnection.sessionId
    });

    window.globalConnection.statsStream = new EventSource('/ssh-stats?' + params.toString());

    window.globalConnection.statsStream.onmessage = function (e) {
        const stats = JSON.parse(e.data);
        updateStatsDisplay(stats);
    };

    window.globalConnection.statsStream.onerror = function () {
        console.error('Stats stream error');
    };
}

// Update stats display in header
function updateStatsDisplay(stats) {
    // Bar mode
    updateResourceBar('cpuBarFillHeader', 'cpuValueHeader', stats.cpu);
    updateResourceBar('ramBarFillHeader', 'ramValueHeader', stats.ram);
    updateResourceBar('diskBarFillHeader', 'diskValueHeader', stats.disk);
    // Text mode
    const fmt = (v) => v.toFixed(1) + '%';
    const cpuT = document.getElementById('cpuValueText');
    const ramT = document.getElementById('ramValueText');
    const dskT = document.getElementById('diskValueText');
    if (cpuT) cpuT.textContent = fmt(stats.cpu);
    if (ramT) ramT.textContent = fmt(stats.ram);
    if (dskT) dskT.textContent = fmt(stats.disk);
}

// Update single resource bar
function updateResourceBar(barId, valueId, percentage) {
    const bar = document.getElementById(barId);
    const value = document.getElementById(valueId);

    if (bar && value) {
        bar.style.width = percentage + '%';
        value.textContent = percentage.toFixed(1) + '%';

        // Update color based on threshold
        bar.classList.remove('low', 'medium', 'high');
        if (percentage < 60) {
            bar.classList.add('low');
        } else if (percentage < 80) {
            bar.classList.add('medium');
        } else {
            bar.classList.add('high');
        }
    }
}

// Disconnect global connection
function disconnectGlobal() {
    if (window.globalConnection.statsStream) {
        window.globalConnection.statsStream.close();
        window.globalConnection.statsStream = null;
    }

    window.dispatchEvent(new CustomEvent('amikoGlobalDisconnect'));

    window.globalConnection.connected = false;
    window.globalConnection.hostname = null;
    window.globalConnection.port = null;
    window.globalConnection.username = null;
    window.globalConnection.password = null;
    window.globalConnection.sessionId = null;
    window.globalConnection.statsStream = null;
    window.globalConnection.serverId = null;
    window.globalConnection.groupId = null;

    sessionStorage.removeItem('globalConnection');

    updateConnectionUI(false);

    console.log('Global connection disconnected');
}

// Get global connection credentials / session reference
function getGlobalCredentials() {
    if (window.globalConnection.connected) {
        return {
            session_id: window.globalConnection.sessionId,
            server_id: window.globalConnection.serverId,
            hostname: window.globalConnection.hostname,
            port: window.globalConnection.port,
            username: window.globalConnection.username
        };
    }
    return null;
}

// Load saved servers into the connection dropdown
function loadSavedServers() {
    fetch('/api/servers/quick-list')
        .then(r => r.json())
        .then(servers => {
            const select = document.getElementById('saved_servers');
            if (!select) return;

            // Clear existing options except defaults
            const isAdmin = window.amikoCurrentUser && window.amikoCurrentUser.role === 'admin';
            select.innerHTML = isAdmin
                ? '<option value="">-- Select --</option><option value="new">+ New Server</option>'
                : '<option value="">-- Select Assigned Server --</option>';

            if (!Array.isArray(servers)) return;

            // Group servers by category
            const groups = {};
            servers.forEach(s => {
                const gName = s.group_name || 'Ungrouped';
                if (!groups[gName]) groups[gName] = [];
                groups[gName].push(s);
            });

            // Create optgroups
            Object.keys(groups).forEach(gName => {
                const optgroup = document.createElement('optgroup');
                optgroup.label = gName;
                groups[gName].forEach(s => {
                    const opt = document.createElement('option');
                    opt.value = JSON.stringify(s);
                    opt.textContent = `${s.name} (${s.hostname})`;
                    optgroup.appendChild(opt);
                });
                select.appendChild(optgroup);
            });
        })
        .catch(err => console.error('[Connection] Error loading saved servers:', err));
}

// Handle server selection from dropdown
document.addEventListener('DOMContentLoaded', function () {
    const select = document.getElementById('saved_servers');
    if (select) {
        select.addEventListener('change', function () {
            const val = this.value;
            const pwInput = document.getElementById('global_password');
            const isAdmin = window.amikoCurrentUser && window.amikoCurrentUser.role === 'admin';
            if (!val || val === 'new') {
                window.globalConnection.serverId = null;
                window.globalConnection.groupId = null;
                if (val === 'new') {
                    document.getElementById('global_hostname').value = '';
                    document.getElementById('global_port').value = 22;
                    document.getElementById('global_username').value = '';
                }
                if (pwInput) {
                    pwInput.value = '';
                    pwInput.placeholder = isAdmin ? '••••••' : 'Stored in Vault 🔒';
                    pwInput.required = isAdmin;
                }
                return;
            }
            try {
                const server = JSON.parse(val);
                window.globalConnection.serverId = server.id || null;
                window.globalConnection.groupId = server.group_id || null;
                document.getElementById('global_hostname').value = server.hostname || '';
                document.getElementById('global_port').value = server.port || 22;
                document.getElementById('global_username').value = server.username || '';
                if (pwInput) {
                    const hasStoredSecret = (server.has_password !== false) || Boolean(server.has_private_key);
                    pwInput.value = '';
                    pwInput.placeholder = hasStoredSecret ? 'Stored in Vault 🔒' : '••••••';
                    pwInput.required = false;
                }
            } catch (e) {
                console.error('[Connection] Error parsing server data:', e);
            }
        });
    }
});
