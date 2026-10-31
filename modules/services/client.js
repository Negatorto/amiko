/*
 * AMIKO - Services & Containers Module
 * Self-contained frontend for managing:
 *   1. System Services & Listening Ports (with 1-click Web Service launcher)
 *   2. Docker & Podman Containers (with port mappings, Web launcher, lifecycle & logs)
 */

class ServicesModule {
    constructor(container) {
        this.container = container;

        // State
        this.activeTab = 'services';
        this.serviceFilter = 'active';
        this.containerFilter = 'all';
        this.servicesData = [];
        this.containersData = [];
        this.containerRuntime = 'none';
        this.currentHostname = '';
        this.autoRefreshTimer = null;
        this.activeLogTarget = null; // { type: 'service'|'container', id: string, runtime?: string }

        // DOM references
        this.tabButtons = container.querySelectorAll('.mod-srv-tab-btn');
        this.panels = container.querySelectorAll('.mod-srv-panel');
        this.btnRefresh = container.querySelector('.mod-srv-btn-refresh');
        this.chkAutoRefresh = container.querySelector('.mod-srv-autorefresh');
        this.hostIndicator = container.querySelector('.mod-srv-host-indicator');

        this.countServicesBadge = container.querySelector('.mod-srv-count-services');
        this.countContainersBadge = container.querySelector('.mod-srv-count-containers');

        // Services tab elements
        this.srvFilterPills = container.querySelectorAll('[data-srv-filter]');
        this.srvSearchInput = container.querySelector('.mod-srv-search-services');
        this.srvTbody = container.querySelector('.mod-srv-tbody-services');

        // Containers tab elements
        this.cntRuntimeBadge = container.querySelector('.mod-srv-runtime-badge');
        this.cntFilterPills = container.querySelectorAll('[data-cnt-filter]');
        this.cntSearchInput = container.querySelector('.mod-srv-search-containers');
        this.cntTbody = container.querySelector('.mod-srv-tbody-containers');

        // Drawer elements
        this.drawer = container.querySelector('.mod-srv-drawer');
        this.drawerTitle = container.querySelector('.mod-srv-drawer-title');
        this.drawerBody = container.querySelector('.mod-srv-drawer-body');
        this.drawerRefreshBtn = container.querySelector('.mod-srv-drawer-refresh');
        this.drawerCloseBtn = container.querySelector('.mod-srv-drawer-close');

        this._bindEvents();

        // Global connection listeners
        this._onConnect = this.onGlobalConnect.bind(this);
        this._onDisconnect = this.onGlobalDisconnect.bind(this);
        window.addEventListener('amikoGlobalConnect', this._onConnect);
        window.addEventListener('amikoGlobalDisconnect', this._onDisconnect);

        if (window.globalConnection && window.globalConnection.connected) {
            this.onGlobalConnect();
        }
    }

    _canWrite() {
        return window.amikoCurrentUser?.capabilities?.can_write !== false;
    }

    _connPayload(extra = {}) {
        const gc = window.globalConnection || {};
        return Object.assign({
            session_id: gc.sessionId,
            server_id: gc.serverId,
            group_id: gc.groupId,
            hostname: gc.hostname,
            port: gc.port,
            username: gc.username
        }, extra);
    }

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str == null ? '' : String(str);
        return div.innerHTML;
    }

    _bindEvents() {
        // Tab switcher
        this.tabButtons.forEach((btn) => {
            btn.addEventListener('click', () => {
                const tab = btn.getAttribute('data-srv-tab');
                this.switchTab(tab);
            });
        });

        // Refresh button
        if (this.btnRefresh) {
            this.btnRefresh.addEventListener('click', () => this.refreshAll());
        }

        // Auto-refresh toggle
        if (this.chkAutoRefresh) {
            this.chkAutoRefresh.addEventListener('change', () => {
                this._updateAutoRefresh();
            });
        }

        // Service filter pills
        this.srvFilterPills.forEach((pill) => {
            pill.addEventListener('click', () => {
                this.serviceFilter = pill.getAttribute('data-srv-filter');
                this.srvFilterPills.forEach(p => p.classList.toggle('active', p === pill));
                this.renderServicesTable();
            });
        });

        if (this.srvSearchInput) {
            this.srvSearchInput.addEventListener('input', () => this.renderServicesTable());
        }

        // Container filter pills
        this.cntFilterPills.forEach((pill) => {
            pill.addEventListener('click', () => {
                this.containerFilter = pill.getAttribute('data-cnt-filter');
                this.cntFilterPills.forEach(p => p.classList.toggle('active', p === pill));
                this.renderContainersTable();
            });
        });

        if (this.cntSearchInput) {
            this.cntSearchInput.addEventListener('input', () => this.renderContainersTable());
        }

        // Delegated action clicks on Services tbody
        if (this.srvTbody) {
            this.srvTbody.addEventListener('click', (e) => {
                const btn = e.target.closest('button[data-srv-action]');
                if (!btn || btn.disabled) return;
                const action = btn.getAttribute('data-srv-action');
                const unit = btn.getAttribute('data-unit');
                const kind = btn.getAttribute('data-kind') || 'systemd';
                const pid = parseInt(btn.getAttribute('data-pid') || '0', 10);

                if (action === 'logs') {
                    this.openLogsDrawer('service', unit);
                } else {
                    this.handleServiceAction(action, unit, kind, pid, btn);
                }
            });
        }

        // Delegated action clicks on Containers tbody
        if (this.cntTbody) {
            this.cntTbody.addEventListener('click', (e) => {
                const btn = e.target.closest('button[data-cnt-action]');
                if (!btn || btn.disabled) return;
                const action = btn.getAttribute('data-cnt-action');
                const cid = btn.getAttribute('data-cid');
                const cname = btn.getAttribute('data-cname') || cid;

                if (action === 'logs') {
                    this.openLogsDrawer('container', cid, cname);
                } else {
                    this.handleContainerAction(action, cid, cname, btn);
                }
            });
        }

        // Drawer buttons
        if (this.drawerCloseBtn) {
            this.drawerCloseBtn.addEventListener('click', () => {
                this.drawer.classList.remove('open');
                this.activeLogTarget = null;
            });
        }
        if (this.drawerRefreshBtn) {
            this.drawerRefreshBtn.addEventListener('click', () => {
                if (this.activeLogTarget) {
                    this.openLogsDrawer(
                        this.activeLogTarget.type,
                        this.activeLogTarget.id,
                        this.activeLogTarget.label
                    );
                }
            });
        }
    }

    switchTab(tabName) {
        this.activeTab = tabName;
        this.tabButtons.forEach(btn => {
            btn.classList.toggle('active', btn.getAttribute('data-srv-tab') === tabName);
        });
        this.panels.forEach(panel => {
            panel.classList.toggle('active', panel.getAttribute('data-srv-panel') === tabName);
        });
    }

    onGlobalConnect() {
        this.currentHostname = window.globalConnection?.hostname || '';
        if (this.hostIndicator) {
            this.hostIndicator.textContent = this.currentHostname ? `Host: ${this.currentHostname}` : 'Connected';
        }
        this.refreshAll();
        this._updateAutoRefresh();
    }

    onGlobalDisconnect() {
        this.currentHostname = '';
        if (this.autoRefreshTimer) {
            clearInterval(this.autoRefreshTimer);
            this.autoRefreshTimer = null;
        }
        if (this.hostIndicator) {
            this.hostIndicator.textContent = 'Not connected';
        }
        this.servicesData = [];
        this.containersData = [];
        if (this.srvTbody) {
            this.srvTbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:28px; color:var(--text-dim);">// Disconnected. Connect to a server to inspect services...</td></tr>`;
        }
        if (this.cntTbody) {
            this.cntTbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:28px; color:var(--text-dim);">// Disconnected. Connect to a server to inspect containers...</td></tr>`;
        }
    }

    _updateAutoRefresh() {
        if (this.autoRefreshTimer) {
            clearInterval(this.autoRefreshTimer);
            this.autoRefreshTimer = null;
        }
        if (this.chkAutoRefresh && this.chkAutoRefresh.checked && window.globalConnection?.connected) {
            this.autoRefreshTimer = setInterval(() => {
                if (window.globalConnection?.connected) {
                    this.refreshAll(true);
                }
            }, 15000);
        }
    }

    async refreshAll(silent = false) {
        if (!window.globalConnection || !window.globalConnection.connected) {
            if (!silent && window.AmikoAlert) {
                window.AmikoAlert('Connection Required', 'Connect to a server first.', 'warning');
            }
            return;
        }
        await Promise.all([
            this.loadServices(silent),
            this.loadContainers(silent)
        ]);
    }

    /* ── System Services Logic ────────────────────────── */
    async loadServices(silent = false) {
        if (!silent && this.srvTbody && this.servicesData.length === 0) {
            this.srvTbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:24px; color:var(--text-dim);">Scanning system services &amp; listening sockets...</td></tr>`;
        }

        try {
            const res = await fetch('/services/overview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(this._connPayload())
            });
            const data = await res.json();
            if (data.status !== 'ok') {
                throw new Error(data.message || 'Failed to load services');
            }

            this.currentHostname = data.hostname || this.currentHostname;
            this.servicesData = data.services || [];

            if (this.countServicesBadge) {
                const activeCount = (data.summary?.running || 0);
                const webCount = (data.summary?.web || 0);
                this.countServicesBadge.textContent = webCount > 0 ? `${activeCount} (${webCount} web)` : `${activeCount}`;
            }

            this.renderServicesTable();
        } catch (err) {
            if (this.srvTbody) {
                this.srvTbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:24px; color:var(--accent-red, #ff5555);">Error: ${this._escapeHtml(err.message)}</td></tr>`;
            }
        }
    }

    renderServicesTable() {
        if (!this.srvTbody) return;

        const q = (this.srvSearchInput?.value || '').trim().toLowerCase();
        const canWrite = this._canWrite();
        const host = this.currentHostname || window.globalConnection?.hostname || 'localhost';

        const filtered = this.servicesData.filter((s) => {
            if (this.serviceFilter === 'active') {
                if (s.active_state !== 'active' && s.active_state !== 'failed' && !s.is_listening) return false;
            } else if (this.serviceFilter === 'web') {
                if (!s.is_web) return false;
            } else if (this.serviceFilter === 'listening') {
                if (!s.is_listening) return false;
            } else if (this.serviceFilter === 'failed') {
                if (s.active_state !== 'failed') return false;
            }

            if (q) {
                const portStr = (s.ports || []).map(p => `:${p.port} ${p.port} ${p.process || ''}`).join(' ').toLowerCase();
                const hay = `${s.name} ${s.unit} ${s.description} ${portStr}`.toLowerCase();
                if (!hay.includes(q)) return false;
            }
            return true;
        });

        if (filtered.length === 0) {
            this.srvTbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:24px; color:var(--text-dim);">// No matching services found for current filter.</td></tr>`;
            return;
        }

        const rowsHtml = filtered.map((s) => {
            // 1. State badge
            let badgeClass = 'st-stopped';
            let stateLabel = s.sub_state || s.active_state || 'inactive';
            if (s.active_state === 'active') {
                badgeClass = 'st-running';
                stateLabel = s.sub_state === 'listening' ? 'LISTENING' : 'RUNNING';
            } else if (s.active_state === 'failed') {
                badgeClass = 'st-failed';
                stateLabel = 'FAILED';
            }

            // 2. Ports & 1-click Web buttons
            let portsHtml = '';
            if (s.ports && s.ports.length > 0) {
                const items = s.ports.map((p) => {
                    const urlHost = host.includes(':') && !host.startsWith('[') ? `[${host}]` : host;
                    const scheme = p.scheme || 'http';
                    const webUrl = `${scheme}://${urlHost}:${p.port}`;
                    const scopeNote = p.scope === 'local' ? ' (127.0.0.1 local)' : '';

                    if (p.is_web) {
                        const icon = scheme === 'https' ? '🔒' : '🌐';
                        return `<a href="${this._escapeHtml(webUrl)}" target="_blank" rel="noopener noreferrer" class="mod-srv-web-btn" title="Open ${this._escapeHtml(webUrl)}${scopeNote} in browser">${icon} OPEN :${p.port}${p.scope === 'local' ? '*' : ''}</a>`;
                    }
                    if (p.scheme) {
                        return `<a href="${this._escapeHtml(webUrl)}" target="_blank" rel="noopener noreferrer" class="mod-srv-port-chip" title="Try opening ${this._escapeHtml(webUrl)}${scopeNote}">${p.proto.toUpperCase()} :${p.port} ↗</a>`;
                    }
                    return `<span class="mod-srv-port-chip" title="Bind: ${this._escapeHtml(p.bind)}">${p.proto.toUpperCase()} :${p.port}</span>`;
                });
                portsHtml = `<div class="mod-srv-ports-cell">${items.join('')}</div>`;
            } else {
                portsHtml = `<span style="color:var(--text-dim); font-size:0.72rem;">—</span>`;
            }

            // 3. Controls
            const isRunning = s.active_state === 'active';
            let actionsHtml = '';

            if (canWrite) {
                if (s.kind === 'systemd') {
                    if (isRunning) {
                        actionsHtml += `<button type="button" class="mod-srv-act-btn" data-srv-action="restart" data-unit="${this._escapeHtml(s.unit)}" data-kind="systemd" title="Restart ${this._escapeHtml(s.name)}">↻ Restart</button>`;
                        if (s.is_protected) {
                            actionsHtml += `<button type="button" class="mod-srv-act-btn act-stop" disabled title="Protected service: stopping '${this._escapeHtml(s.name)}' would disconnect your SSH session">🔒 Stop</button>`;
                        } else {
                            actionsHtml += `<button type="button" class="mod-srv-act-btn act-stop" data-srv-action="stop" data-unit="${this._escapeHtml(s.unit)}" data-kind="systemd" title="Stop ${this._escapeHtml(s.name)}">■ Stop</button>`;
                        }
                    } else {
                        actionsHtml += `<button type="button" class="mod-srv-act-btn act-start" data-srv-action="start" data-unit="${this._escapeHtml(s.unit)}" data-kind="systemd" title="Start ${this._escapeHtml(s.name)}">▶ Start</button>`;
                    }
                } else if (s.kind === 'process' && s.pid > 1) {
                    actionsHtml += `<button type="button" class="mod-srv-act-btn act-stop" data-srv-action="stop" data-unit="${this._escapeHtml(s.name)}" data-kind="process" data-pid="${s.pid}" title="Terminate PID ${s.pid}">■ Kill</button>`;
                }
            }

            if (s.kind === 'systemd') {
                actionsHtml += `<button type="button" class="mod-srv-act-btn" data-srv-action="logs" data-unit="${this._escapeHtml(s.unit)}" title="View systemctl status &amp; journal logs">📄</button>`;
            }

            const kindBadge = s.kind === 'process'
                ? `<span style="font-size:0.62rem; padding:1px 4px; border:1px solid var(--text-dim); color:var(--text-dim); border-radius:2px;">PID ${s.pid || ''}</span>`
                : '';

            return `
                <tr>
                    <td>
                        <div class="mod-srv-name">
                            <span>${this._escapeHtml(s.name)}</span>
                            ${kindBadge}
                        </div>
                        <div class="mod-srv-desc" title="${this._escapeHtml(s.description)}">${this._escapeHtml(s.description)}</div>
                    </td>
                    <td>
                        <span class="mod-srv-badge ${badgeClass}">● ${this._escapeHtml(stateLabel)}</span>
                    </td>
                    <td>${portsHtml}</td>
                    <td>
                        <div class="mod-srv-actions">${actionsHtml}</div>
                    </td>
                </tr>
            `;
        });

        this.srvTbody.innerHTML = rowsHtml.join('');
    }

    handleServiceAction(action, unit, kind, pid, btnEl) {
        const doExec = async () => {
            const origText = btnEl.textContent;
            btnEl.disabled = true;
            btnEl.textContent = '...';

            try {
                const res = await fetch('/services/action', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this._connPayload({ action, unit, kind, pid }))
                });
                const data = await res.json();
                if (data.status !== 'ok') {
                    if (window.AmikoAlert) window.AmikoAlert('Service Error', data.message || 'Operation failed', 'error');
                }
                await this.loadServices(true);
            } catch (err) {
                if (window.AmikoAlert) window.AmikoAlert('Network Error', err.message, 'error');
            } finally {
                btnEl.disabled = false;
                btnEl.textContent = origText;
            }
        };

        if (action === 'stop') {
            const msg = `Are you sure you want to stop '${unit}'?`;
            if (window.AmikoConfirm) {
                window.AmikoConfirm('Confirm Stop Service', msg, doExec);
            } else if (confirm(msg)) {
                doExec();
            }
        } else {
            doExec();
        }
    }

    /* ── Containers Logic (Docker / Podman) ───────────── */
    async loadContainers(silent = false) {
        if (!silent && this.cntTbody && this.containersData.length === 0) {
            this.cntTbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:24px; color:var(--text-dim);">Checking Docker / Podman containers...</td></tr>`;
        }

        try {
            const res = await fetch('/services/containers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(this._connPayload())
            });
            const data = await res.json();
            if (data.status !== 'ok') {
                throw new Error(data.message || 'Failed to query containers');
            }

            this.containerRuntime = data.runtime || 'none';
            this.containersData = data.containers || [];

            if (this.cntRuntimeBadge) {
                this.cntRuntimeBadge.textContent = this.containerRuntime === 'none'
                    ? 'RUNTIME: NONE'
                    : `RUNTIME: ${this.containerRuntime.toUpperCase()}`;
            }

            if (this.countContainersBadge) {
                const running = data.summary?.running || 0;
                const total = data.summary?.total || 0;
                this.countContainersBadge.textContent = `${running}/${total}`;
            }

            this.renderContainersTable();
        } catch (err) {
            if (this.cntTbody) {
                this.cntTbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:24px; color:var(--accent-red, #ff5555);">Error: ${this._escapeHtml(err.message)}</td></tr>`;
            }
        }
    }

    renderContainersTable() {
        if (!this.cntTbody) return;

        if (this.containerRuntime === 'none') {
            this.cntTbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:28px; color:var(--text-dim);">// Neither Docker nor Podman was detected on this server.</td></tr>`;
            return;
        }

        const q = (this.cntSearchInput?.value || '').trim().toLowerCase();
        const canWrite = this._canWrite();
        const host = this.currentHostname || window.globalConnection?.hostname || 'localhost';

        const filtered = this.containersData.filter((c) => {
            if (this.containerFilter === 'running' && c.state !== 'running') return false;
            if (this.containerFilter === 'web' && !c.is_web) return false;
            if (this.containerFilter === 'stopped' && c.state === 'running') return false;

            if (q) {
                const hay = `${c.name} ${c.id} ${c.image} ${c.status} ${c.ports_raw || ''}`.toLowerCase();
                if (!hay.includes(q)) return false;
            }
            return true;
        });

        if (filtered.length === 0) {
            this.cntTbody.innerHTML = `<tr><td colspan="4" style="text-align:center; padding:24px; color:var(--text-dim);">// No containers match the current filter.</td></tr>`;
            return;
        }

        const rowsHtml = filtered.map((c) => {
            let badgeClass = 'st-stopped';
            if (c.state === 'running') badgeClass = 'st-running';
            else if (c.state === 'paused' || c.state === 'restarting') badgeClass = 'st-paused';
            else if (c.state === 'dead') badgeClass = 'st-failed';

            // Published ports & 1-click Web launcher
            let portsHtml = '';
            if (c.ports && c.ports.length > 0) {
                const items = c.ports.map((p) => {
                    const urlHost = host.includes(':') && !host.startsWith('[') ? `[${host}]` : host;
                    const scheme = p.scheme || 'http';
                    const webUrl = `${scheme}://${urlHost}:${p.host_port}`;
                    const mapLabel = p.host_port === p.container_port
                        ? `:${p.host_port}`
                        : `:${p.host_port}→${p.container_port}`;

                    if (c.state === 'running' && p.is_web) {
                        const icon = scheme === 'https' ? '🔒' : '🌐';
                        return `<a href="${this._escapeHtml(webUrl)}" target="_blank" rel="noopener noreferrer" class="mod-srv-web-btn" title="Open container web UI (${this._escapeHtml(webUrl)})">${icon} OPEN :${p.host_port}</a>`;
                    }
                    if (c.state === 'running' && p.scheme) {
                        return `<a href="${this._escapeHtml(webUrl)}" target="_blank" rel="noopener noreferrer" class="mod-srv-port-chip" title="Open ${this._escapeHtml(webUrl)}">${mapLabel}/${p.proto} ↗</a>`;
                    }
                    return `<span class="mod-srv-port-chip">${mapLabel}/${p.proto}</span>`;
                });
                portsHtml = `<div class="mod-srv-ports-cell">${items.join('')}</div>`;
            } else {
                portsHtml = `<span style="color:var(--text-dim); font-size:0.72rem;">—</span>`;
            }

            // Actions
            let actionsHtml = '';
            if (canWrite) {
                if (c.state === 'running') {
                    actionsHtml += `<button type="button" class="mod-srv-act-btn" data-cnt-action="restart" data-cid="${this._escapeHtml(c.id)}" data-cname="${this._escapeHtml(c.name)}" title="Restart container">↻ Restart</button>`;
                    actionsHtml += `<button type="button" class="mod-srv-act-btn act-stop" data-cnt-action="stop" data-cid="${this._escapeHtml(c.id)}" data-cname="${this._escapeHtml(c.name)}" title="Stop container">■ Stop</button>`;
                } else {
                    actionsHtml += `<button type="button" class="mod-srv-act-btn act-start" data-cnt-action="start" data-cid="${this._escapeHtml(c.id)}" data-cname="${this._escapeHtml(c.name)}" title="Start container">▶ Start</button>`;
                }
            }
            actionsHtml += `<button type="button" class="mod-srv-act-btn" data-cnt-action="logs" data-cid="${this._escapeHtml(c.id)}" data-cname="${this._escapeHtml(c.name)}" title="View container logs">📄</button>`;

            return `
                <tr>
                    <td>
                        <div class="mod-srv-name">
                            <span>${this._escapeHtml(c.name)}</span>
                            <span style="font-size:0.65rem; color:var(--text-dim); font-weight:normal;">#${this._escapeHtml(c.id.slice(0, 8))}</span>
                        </div>
                        <div class="mod-srv-desc" title="${this._escapeHtml(c.image)}">${this._escapeHtml(c.image)}</div>
                    </td>
                    <td>
                        <span class="mod-srv-badge ${badgeClass}">● ${this._escapeHtml(c.state.toUpperCase())}</span>
                        <div style="font-size:0.68rem; color:var(--text-dim); margin-top:2px;">${this._escapeHtml(c.status)}</div>
                    </td>
                    <td>${portsHtml}</td>
                    <td>
                        <div class="mod-srv-actions">${actionsHtml}</div>
                    </td>
                </tr>
            `;
        });

        this.cntTbody.innerHTML = rowsHtml.join('');
    }

    handleContainerAction(action, containerId, containerName, btnEl) {
        const doExec = async () => {
            const origText = btnEl.textContent;
            btnEl.disabled = true;
            btnEl.textContent = '...';

            try {
                const res = await fetch('/services/container-action', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this._connPayload({
                        action,
                        container_id: containerId,
                        runtime: this.containerRuntime
                    }))
                });
                const data = await res.json();
                if (data.status !== 'ok') {
                    if (window.AmikoAlert) window.AmikoAlert('Container Error', data.message || 'Operation failed', 'error');
                }
                await Promise.all([this.loadContainers(true), this.loadServices(true)]);
            } catch (err) {
                if (window.AmikoAlert) window.AmikoAlert('Network Error', err.message, 'error');
            } finally {
                btnEl.disabled = false;
                btnEl.textContent = origText;
            }
        };

        if (action === 'stop') {
            const msg = `Stop container '${containerName}'?`;
            if (window.AmikoConfirm) {
                window.AmikoConfirm('Confirm Stop Container', msg, doExec);
            } else if (confirm(msg)) {
                doExec();
            }
        } else {
            doExec();
        }
    }

    /* ── Logs & Status Inspector Drawer ───────────────── */
    async openLogsDrawer(type, id, label = '') {
        if (!this.drawer || !this.drawerBody) return;
        this.activeLogTarget = { type, id, label: label || id };
        this.drawer.classList.add('open');
        if (this.drawerTitle) {
            this.drawerTitle.textContent = type === 'container'
                ? `Container Logs — ${label || id}`
                : `Service Status & Logs — ${id}`;
        }
        this.drawerBody.textContent = '// Fetching remote logs...';

        try {
            const endpoint = type === 'container' ? '/services/container-logs' : '/services/logs';
            const payload = type === 'container'
                ? this._connPayload({ container_id: id, runtime: this.containerRuntime })
                : this._connPayload({ unit: id });

            const res = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (data.status !== 'ok') {
                this.drawerBody.textContent = `Error: ${data.message || 'Unable to load logs.'}`;
                return;
            }
            this.drawerBody.textContent = data.logs || 'No log entries.';
            this.drawerBody.scrollTop = this.drawerBody.scrollHeight;
        } catch (err) {
            this.drawerBody.textContent = `Network error: ${err.message}`;
        }
    }

    destroy() {
        if (this.autoRefreshTimer) {
            clearInterval(this.autoRefreshTimer);
            this.autoRefreshTimer = null;
        }
        window.removeEventListener('amikoGlobalConnect', this._onConnect);
        window.removeEventListener('amikoGlobalDisconnect', this._onDisconnect);
    }
}

AmikoModules.register('services', ServicesModule);

