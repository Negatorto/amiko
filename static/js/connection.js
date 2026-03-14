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
    statsStream: null
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

        globalConnectBtn.disabled = true;
        globalConnectBtn.textContent = 'CONNECTING...';

        try {
            // Test connection via SSH endpoint
            const response = await fetch('/ssh-connect', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ hostname, port, username, password })
            });

            const result = await response.json();

            if (result.success) {
                // ... setup connection ... (logic unchanged)
                window.globalConnection.connected = true;
                window.globalConnection.hostname = hostname;
                window.globalConnection.port = port;
                window.globalConnection.username = username;
                window.globalConnection.password = password;
                window.globalConnection.sessionId = result.session_id;

                updateConnectionUI(true, hostname, port, username);

                sessionStorage.setItem('globalConnection', JSON.stringify({
                    hostname, port, username, password
                }));

                startStatsMonitoring();

                window.dispatchEvent(new CustomEvent('amikoGlobalConnect', { detail: { hostname, port, username } }));
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

    // Check if there's a saved connection
    const savedConnection = sessionStorage.getItem('globalConnection');
    if (savedConnection) {
        const conn = JSON.parse(savedConnection);
        document.getElementById('global_hostname').value = conn.hostname;
        document.getElementById('global_port').value = conn.port;
        document.getElementById('global_username').value = conn.username;
        document.getElementById('global_password').value = conn.password;
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
        resourceMonitorHeader.classList.remove('hidden');
        globalConnectBtn.classList.add('hidden');
        globalDisconnectBtn.classList.remove('hidden');

        statusDot.classList.add('connected');
        statusText.textContent = 'CONNECTED';
        connectionInfo.textContent = `${username}@${hostname}:${port}`;
    } else {
        connectionForm.classList.remove('hidden');
        connectionStatus.classList.add('hidden');
        resourceMonitorHeader.classList.add('hidden');
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
        hostname: window.globalConnection.hostname,
        port: window.globalConnection.port,
        username: window.globalConnection.username,
        password: window.globalConnection.password
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
    updateResourceBar('cpuBarFillHeader', 'cpuValueHeader', stats.cpu);
    updateResourceBar('ramBarFillHeader', 'ramValueHeader', stats.ram);
    updateResourceBar('diskBarFillHeader', 'diskValueHeader', stats.disk);
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

    sessionStorage.removeItem('globalConnection');

    updateConnectionUI(false);

    console.log('Global connection disconnected');
}

// Get global connection credentials
function getGlobalCredentials() {
    if (window.globalConnection.connected) {
        return {
            hostname: window.globalConnection.hostname,
            port: window.globalConnection.port,
            username: window.globalConnection.username,
            password: window.globalConnection.password
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
            select.innerHTML = '<option value="">-- Select --</option><option value="new">+ New Server</option>';

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
            if (!val || val === 'new') return;
            try {
                const server = JSON.parse(val);
                document.getElementById('global_hostname').value = server.hostname || '';
                document.getElementById('global_port').value = server.port || 22;
                document.getElementById('global_username').value = server.username || '';
                document.getElementById('global_password').value = server.password || '';
            } catch (e) {
                console.error('[Connection] Error parsing server data:', e);
            }
        });
    }
});
