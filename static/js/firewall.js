/*
 * AMIKO - Firewall & Fail2Ban Settings Panel
 * 4 views: Traffic Rules, Ports & Services, Fail2Ban Jails & Whitelist, Banned IPs (real-time)
 * Visible to admins and to operators holding the delegated 'can_manage_firewall' permission.
 */

class FirewallPanel {
    constructor(root) {
        this.root = root;
        this.view = 'rules';
        this.state = null;
        this.editingRuleId = null;
        this.editingServiceId = null;
        this.refreshTimer = null;
        this.countdownTimer = null;
    }

    // --- Lifecycle ---

    async load() {
        await this.refresh();
        this.startTimers();
    }

    stop() {
        clearInterval(this.refreshTimer);
        clearInterval(this.countdownTimer);
        this.refreshTimer = this.countdownTimer = null;
    }

    startTimers() {
        this.stop();
        // Real-time ban monitoring while the Banned IPs view is open
        this.refreshTimer = setInterval(() => {
            if (this.view === 'bans' && this.root.offsetParent !== null) this.refreshBans();
        }, 5000);
        this.countdownTimer = setInterval(() => this.tickCountdowns(), 1000);
    }

    async refresh() {
        const res = await this.api('GET', '/api/firewall');
        if (res.error) {
            this.root.innerHTML = `<div class="settings-empty">${this.esc(res.error)}</div>`;
            return;
        }
        this.state = res;
        this.render();
    }

    async refreshBans() {
        const bans = await this.api('GET', '/api/firewall/bans');
        if (!Array.isArray(bans) || !this.state) return;
        this.state.bans = bans;
        const wrap = this.root.querySelector('#fwBansTable');
        if (wrap) wrap.innerHTML = this.bansTableHtml();
        this.bindBanActions();
        this.updateBanCounter();
    }

    // --- Rendering ---

    render() {
        const views = [
            ['rules', 'Traffic Rules'],
            ['services', 'Ports & Services'],
            ['fail2ban', 'Fail2Ban Jails'],
            ['bans', 'Banned IPs']
        ];
        this.root.innerHTML = `
            <div class="fw-subtabs">
                ${views.map(([id, label]) => `
                    <button type="button" class="fw-subtab ${this.view === id ? 'active' : ''}" data-view="${id}">
                        ${label}${id === 'bans' ? ` <span class="fw-count" id="fwBanCount">${this.state.bans.length}</span>` : ''}
                    </button>`).join('')}
            </div>
            <div class="fw-view">${this.viewHtml()}</div>
        `;
        this.root.querySelectorAll('.fw-subtab').forEach(btn => {
            btn.addEventListener('click', () => {
                this.view = btn.dataset.view;
                this.editingRuleId = this.editingServiceId = null;
                this.render();
            });
        });
        this.bindView();
    }

    viewHtml() {
        if (this.view === 'services') return this.servicesHtml();
        if (this.view === 'fail2ban') return this.fail2banHtml();
        if (this.view === 'bans') return this.bansHtml();
        return this.rulesHtml();
    }

    bindView() {
        if (this.view === 'services') this.bindServices();
        else if (this.view === 'fail2ban') this.bindFail2ban();
        else if (this.view === 'bans') this.bindBans();
        else this.bindRules();
    }

    // ========================
    // 1. TRAFFIC RULES
    // ========================

    isL7Applicable(rule) {
        if (rule.protocol === 'UDP' || rule.protocol === 'ICMP') return false;
        const svc = this.state.services.find(s => s.name === rule.service);
        return !(svc && svc.protocol !== 'TCP');
    }

    rulesHtml() {
        const fw = this.state.firewall;
        const ks = this.state.kernel_sync;
        const rules = fw.rules;

        const rows = rules.map((r, i) => {
            const l7 = this.isL7Applicable(r);
            return `
                <tr class="${r.enabled ? '' : 'fw-row-disabled'}">
                    <td class="fw-num">${i + 1}</td>
                    <td><input type="checkbox" class="fw-rule-toggle" data-id="${this.attr(r.id)}" ${r.enabled ? 'checked' : ''} title="Enable / disable rule"></td>
                    <td>${this.esc(r.chain)}</td>
                    <td>${this.esc(r.interface)}</td>
                    <td class="fw-mono">${this.esc(r.source)}</td>
                    <td class="fw-mono">${this.esc(r.destination)}</td>
                    <td>${this.esc(r.protocol)}</td>
                    <td>${this.esc(r.service)}</td>
                    <td>${this.esc(r.state)}</td>
                    <td><span class="fw-target fw-target-${r.target.toLowerCase()}">${this.esc(r.target)}</span>
                        ${l7 ? '' : '<span class="fw-badge" title="UDP/ICMP traffic does not reach AMIKO: this rule is not enforced at the application layer (L7).">kernel-only</span>'}</td>
                    <td class="fw-comment" title="${this.attr(r.comment)}">${this.esc(r.comment)}</td>
                    <td class="fw-actions">
                        <button type="button" class="fw-move" data-id="${this.attr(r.id)}" data-dir="up" title="Move up" ${i === 0 ? 'disabled' : ''}>▲</button>
                        <button type="button" class="fw-move" data-id="${this.attr(r.id)}" data-dir="down" title="Move down" ${i === rules.length - 1 ? 'disabled' : ''}>▼</button>
                        <button type="button" class="fw-edit-rule" data-id="${this.attr(r.id)}" title="Edit"><svg class="md-icon"><use href="#md-edit"></use></svg></button>
                        <button type="button" class="fw-del-rule btn-delete" data-id="${this.attr(r.id)}" title="Delete"><svg class="md-icon"><use href="#md-delete"></use></svg></button>
                    </td>
                </tr>`;
        }).join('');

        const editing = this.editingRuleId ? rules.find(r => r.id === this.editingRuleId) : null;
        const v = editing || { chain: 'INPUT', interface: 'ANY', source: '0.0.0.0/0', destination: '0.0.0.0/0', protocol: 'ALL', service: 'ANY', state: 'ANY', target: 'ACCEPT', comment: '' };
        const opt = (values, sel) => values.map(x => `<option value="${this.attr(x)}" ${x === sel ? 'selected' : ''}>${this.esc(x)}</option>`).join('');
        const ifaces = ['ANY', ...this.state.interfaces.filter(n => n !== 'ANY')];
        if (!ifaces.includes(v.interface)) ifaces.push(v.interface);
        const serviceNames = ['ANY', ...this.state.services.map(s => s.name)];
        const isLiteralPort = !serviceNames.includes(v.service);

        return `
            <div class="fw-toolbar">
                <label class="fw-switch">
                    <input type="checkbox" class="theme-toggle-checkbox" id="fwEnabled" ${fw.enabled ? 'checked' : ''}>
                    <span><strong>FIREWALL ${fw.enabled ? 'ACTIVE' : 'DISABLED'}</strong></span>
                </label>
                <label>INPUT policy
                    <select id="fwInputPolicy">${opt(['ACCEPT', 'DROP'], fw.input_policy)}</select>
                </label>
                <label>OUTPUT policy
                    <select id="fwOutputPolicy">${opt(['ACCEPT', 'DROP'], fw.output_policy)}</select>
                </label>
                <span class="fw-info" title="Rules are checked against this address by the anti-lockout safeguard">Your IP: <strong>${this.esc(this.state.client_ip)}</strong></span>
            </div>
            <div class="fw-kernel">
                <label class="fw-switch" title="${this.attr(ks.available ? 'Mirror bans into ' + ks.backend : ks.error)}">
                    <input type="checkbox" class="theme-toggle-checkbox" id="fwKernelSync" ${ks.enabled ? 'checked' : ''} ${(!ks.available && !ks.enabled) || !this.state.can_manage_kernel ? 'disabled' : ''}>
                    <span>Kernel sync (Level 2)</span>
                </label>
                <span class="settings-hint">${ks.available
                    ? `Backend <strong>${this.esc(ks.backend)}</strong> available: bans can be enforced in the Linux kernel.`
                    : this.esc(ks.error || 'Not available')} ${this.state.can_manage_kernel ? '' : '(admin only)'}</span>
            </div>
            <p class="settings-hint">Rules are evaluated top-down, the first match wins; unmatched traffic follows the chain policy.
                Loopback is always accepted and the safeguard refuses changes that would cut off your current connection.
                Break-glass: <code>python3 utils/reset_firewall.py --disable</code>.</p>

            <div class="server-table-wrapper">
                ${rules.length ? `
                <table class="server-table fw-table">
                    <thead><tr><th>#</th><th>On</th><th>Traffic</th><th>Iface</th><th>Source</th><th>Destination</th><th>Proto</th><th>Port/Service</th><th>State</th><th>Target</th><th>Comment</th><th></th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>` : '<div class="settings-empty">No rules defined: all traffic follows the default policy.</div>'}
            </div>

            <form class="fw-form" id="fwRuleForm">
                <div class="fw-form-title">${editing ? `Edit rule ${this.esc(editing.id)}` : 'Add rule'}</div>
                <div class="form-field"><label>Traffic</label><select name="chain">${opt(['INPUT', 'OUTPUT'], v.chain)}</select></div>
                <div class="form-field"><label>Interface</label><select name="interface">${opt(ifaces, v.interface)}</select></div>
                <div class="form-field"><label>Source IP/Subnet</label><input name="source" value="${this.attr(v.source)}" placeholder="0.0.0.0/0"></div>
                <div class="form-field"><label>Destination IP/Subnet</label><input name="destination" value="${this.attr(v.destination)}" placeholder="0.0.0.0/0"></div>
                <div class="form-field"><label>Protocol</label><select name="protocol">${opt(['ALL', 'TCP', 'UDP', 'ICMP'], v.protocol)}</select></div>
                <div class="form-field"><label>Port/Service</label>
                    <select name="service">${opt(serviceNames, isLiteralPort ? '' : v.service)}<option value="__custom" ${isLiteralPort ? 'selected' : ''}>Custom port…</option></select>
                    <input name="custom_port" class="fw-custom-port" value="${isLiteralPort ? this.attr(v.service) : ''}" placeholder="e.g. 8000-8100" style="${isLiteralPort ? '' : 'display:none;'}">
                </div>
                <div class="form-field"><label>State</label><select name="state">${opt(['ANY', 'NEW', 'ESTABLISHED'], v.state)}</select></div>
                <div class="form-field"><label>Target</label><select name="target">${opt(['ACCEPT', 'DROP', 'REJECT'], v.target)}</select></div>
                <div class="form-field fw-grow"><label>Comment</label><input name="comment" value="${this.attr(v.comment)}" maxlength="120"></div>
                <div class="fw-form-actions">
                    ${editing ? '<button type="button" id="fwRuleCancel">Cancel</button>' : ''}
                    <button type="submit">${editing ? 'SAVE' : '+ ADD RULE'}</button>
                </div>
            </form>
        `;
    }

    bindRules() {
        const fw = this.state.firewall;
        const $ = (sel) => this.root.querySelector(sel);

        $('#fwEnabled').addEventListener('change', async (e) => {
            const enabling = e.target.checked;
            const apply = async () => {
                const r = await this.api('PUT', '/api/firewall/settings', { enabled: enabling });
                this.handleResult(r);
                this.refresh();
            };
            if (enabling && (fw.input_policy === 'DROP' || fw.output_policy === 'DROP')) {
                // Re-sync the switch with the server state; it flips only once the dialog is confirmed
                this.refresh();
                this.confirm('Enable Firewall', 'A DROP default policy is configured: traffic not explicitly accepted by a rule will be blocked. Continue?', apply);
            } else {
                apply();
            }
        });

        ['fwInputPolicy', 'fwOutputPolicy'].forEach(id => {
            $(`#${id}`).addEventListener('change', async (e) => {
                const key = id === 'fwInputPolicy' ? 'input_policy' : 'output_policy';
                const r = await this.api('PUT', '/api/firewall/settings', { [key]: e.target.value });
                this.handleResult(r);
                this.refresh();
            });
        });

        $('#fwKernelSync').addEventListener('change', async (e) => {
            const r = await this.api('PUT', '/api/firewall/kernel', { enabled: e.target.checked });
            this.handleResult(r);
            this.refresh();
        });

        this.root.querySelectorAll('.fw-rule-toggle').forEach(cb => {
            cb.addEventListener('change', async () => {
                const r = await this.api('PUT', `/api/firewall/rules/${encodeURIComponent(cb.dataset.id)}`, { enabled: cb.checked });
                this.handleResult(r);
                this.refresh();
            });
        });

        this.root.querySelectorAll('.fw-move').forEach(btn => {
            btn.addEventListener('click', async () => {
                const r = await this.api('POST', `/api/firewall/rules/${encodeURIComponent(btn.dataset.id)}/move`, { direction: btn.dataset.dir });
                this.handleResult(r);
                this.refresh();
            });
        });

        this.root.querySelectorAll('.fw-edit-rule').forEach(btn => {
            btn.addEventListener('click', () => {
                this.editingRuleId = btn.dataset.id;
                this.render();
                const form = this.root.querySelector('#fwRuleForm');
                if (form) form.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            });
        });

        this.root.querySelectorAll('.fw-del-rule').forEach(btn => {
            btn.addEventListener('click', () => {
                this.confirm('Delete Rule', `Delete firewall rule ${btn.dataset.id}?`, async () => {
                    const r = await this.api('DELETE', `/api/firewall/rules/${encodeURIComponent(btn.dataset.id)}`);
                    this.handleResult(r);
                    this.refresh();
                });
            });
        });

        const form = $('#fwRuleForm');
        const serviceSel = form.querySelector('[name="service"]');
        const customPort = form.querySelector('[name="custom_port"]');
        serviceSel.addEventListener('change', () => {
            customPort.style.display = serviceSel.value === '__custom' ? '' : 'none';
        });

        const cancel = $('#fwRuleCancel');
        if (cancel) cancel.addEventListener('click', () => { this.editingRuleId = null; this.render(); });

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const data = Object.fromEntries(new FormData(form).entries());
            data.service = data.service === '__custom' ? (data.custom_port || '').trim() : data.service;
            delete data.custom_port;
            const r = this.editingRuleId
                ? await this.api('PUT', `/api/firewall/rules/${encodeURIComponent(this.editingRuleId)}`, data)
                : await this.api('POST', '/api/firewall/rules', data);
            if (this.handleResult(r)) {
                this.editingRuleId = null;
                this.refresh();
            }
        });
    }

    // ========================
    // 2. PORTS & SERVICES
    // ========================

    servicesHtml() {
        const services = this.state.services;
        const used = new Set(this.state.firewall.rules.map(r => r.service));
        const editing = this.editingServiceId ? services.find(s => s.id === this.editingServiceId) : null;
        const v = editing || { name: '', protocol: 'TCP', ports: '', description: '' };
        const opt = (values, sel) => values.map(x => `<option value="${x}" ${x === sel ? 'selected' : ''}>${x}</option>`).join('');

        const rows = services.map(s => `
            <tr>
                <td><strong>${this.esc(s.name)}</strong></td>
                <td>${this.esc(s.protocol)}</td>
                <td class="fw-mono">${this.esc(s.ports || 'any')}${s.protocol === 'ICMP' ? ' <span class="fw-dim">(type)</span>' : ''}</td>
                <td>${this.esc(s.description)}</td>
                <td>${used.has(s.name) ? '<span class="fw-badge">in use</span>' : ''}</td>
                <td class="fw-actions">
                    <button type="button" class="fw-edit-svc" data-id="${this.attr(s.id)}" title="Edit"><svg class="md-icon"><use href="#md-edit"></use></svg></button>
                    <button type="button" class="fw-del-svc btn-delete" data-id="${this.attr(s.id)}" data-name="${this.attr(s.name)}" title="Delete" ${used.has(s.name) ? 'disabled' : ''}><svg class="md-icon"><use href="#md-delete"></use></svg></button>
                </td>
            </tr>`).join('');

        return `
            <p class="settings-hint">Named services can be referenced by firewall rules. Renaming a service updates the rules that use it.</p>
            <div class="server-table-wrapper">
                <table class="server-table fw-table">
                    <thead><tr><th>Name</th><th>Protocol</th><th>Ports</th><th>Description</th><th></th><th></th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
            <form class="fw-form" id="fwServiceForm">
                <div class="fw-form-title">${editing ? `Edit ${this.esc(editing.name)}` : 'Define service'}</div>
                <div class="form-field"><label>Name</label><input name="name" value="${this.attr(v.name)}" placeholder="e.g. POSTGRES" required maxlength="32"></div>
                <div class="form-field"><label>Protocol</label><select name="protocol">${opt(['TCP', 'UDP', 'ICMP'], v.protocol)}</select></div>
                <div class="form-field"><label>Port / Range / ICMP type</label><input name="ports" value="${this.attr(v.ports)}" placeholder="5432 or 8000-8100 or 22,443"></div>
                <div class="form-field fw-grow"><label>Description</label><input name="description" value="${this.attr(v.description)}" maxlength="120"></div>
                <div class="fw-form-actions">
                    ${editing ? '<button type="button" id="fwSvcCancel">Cancel</button>' : ''}
                    <button type="submit">${editing ? 'SAVE' : '+ ADD SERVICE'}</button>
                </div>
            </form>
        `;
    }

    bindServices() {
        this.root.querySelectorAll('.fw-edit-svc').forEach(btn => {
            btn.addEventListener('click', () => { this.editingServiceId = btn.dataset.id; this.render(); });
        });
        this.root.querySelectorAll('.fw-del-svc').forEach(btn => {
            btn.addEventListener('click', () => {
                this.confirm('Delete Service', `Delete service "${btn.dataset.name}"?`, async () => {
                    const r = await this.api('DELETE', `/api/firewall/services/${encodeURIComponent(btn.dataset.id)}`);
                    this.handleResult(r);
                    this.refresh();
                });
            });
        });
        const cancel = this.root.querySelector('#fwSvcCancel');
        if (cancel) cancel.addEventListener('click', () => { this.editingServiceId = null; this.render(); });

        const form = this.root.querySelector('#fwServiceForm');
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const data = Object.fromEntries(new FormData(form).entries());
            const r = this.editingServiceId
                ? await this.api('PUT', `/api/firewall/services/${encodeURIComponent(this.editingServiceId)}`, data)
                : await this.api('POST', '/api/firewall/services', data);
            if (this.handleResult(r)) {
                this.editingServiceId = null;
                this.refresh();
            }
        });
    }

    // ========================
    // 3. FAIL2BAN JAILS & WHITELIST
    // ========================

    fail2banHtml() {
        const f2b = this.state.fail2ban;
        const rows = Object.entries(f2b.jails).map(([name, j]) => `
            <tr data-jail="${this.attr(name)}">
                <td><input type="checkbox" class="fw-jail-enabled" ${j.enabled ? 'checked' : ''}></td>
                <td><strong>${this.esc(name)}</strong><div class="fw-dim">${this.esc(j.description || '')}</div></td>
                <td><input type="number" class="fw-jail-input" data-key="max_retry" min="1" max="1000" value="${j.max_retry}"></td>
                <td><input type="number" class="fw-jail-input" data-key="find_time" min="1" value="${j.find_time}"> <span class="fw-dim">min</span></td>
                <td><input type="number" class="fw-jail-input" data-key="ban_time" min="-1" value="${j.ban_time}"> <span class="fw-dim">min</span></td>
                <td class="fw-actions"><button type="button" class="fw-save-jail" title="Save jail"><svg class="md-icon"><use href="#md-save"></use></svg></button></td>
            </tr>`).join('');

        return `
            <div class="fw-toolbar">
                <label class="fw-switch">
                    <input type="checkbox" class="theme-toggle-checkbox" id="fwF2bEnabled" ${f2b.enabled ? 'checked' : ''}>
                    <span><strong>FAIL2BAN ${f2b.enabled ? 'ACTIVE' : 'DISABLED'}</strong></span>
                </label>
            </div>
            <div class="server-table-wrapper">
                <table class="server-table fw-table">
                    <thead><tr><th>On</th><th>Jail</th><th>Max Retry</th><th>Find Time</th><th>Ban Time (-1 = permanent)</th><th></th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
            <p class="settings-hint"><strong>recidive</strong>: <em>Max Retry</em> is the number of bans by other jails within <em>Find Time</em> that triggers the long ban.</p>

            <h4 class="fw-subtitle">Global Whitelist (Ignore IPs / Subnets)</h4>
            <p class="settings-hint">Trusted addresses are never banned automatically. One IP or CIDR per line. Loopback and trusted reverse proxies (AMIKO_TRUSTED_PROXIES) are always exempt.</p>
            <textarea id="fwIgnoreIps" class="fw-textarea" rows="5" placeholder="192.168.1.0/24&#10;10.0.0.5">${this.esc(f2b.ignore_ips.join('\n'))}</textarea>
            <div class="fw-form-actions"><button type="button" id="fwSaveWhitelist" class="btn-action-primary">Save whitelist</button></div>
        `;
    }

    bindFail2ban() {
        this.root.querySelector('#fwF2bEnabled').addEventListener('change', async (e) => {
            const r = await this.api('PUT', '/api/firewall/fail2ban', { enabled: e.target.checked });
            this.handleResult(r);
            this.refresh();
        });
        this.root.querySelector('#fwSaveWhitelist').addEventListener('click', async () => {
            const lines = this.root.querySelector('#fwIgnoreIps').value.split('\n').map(s => s.trim()).filter(Boolean);
            const r = await this.api('PUT', '/api/firewall/fail2ban', { ignore_ips: lines });
            if (this.handleResult(r)) {
                this.alert('Whitelist saved', `${r.ignore_ips.length} trusted entr${r.ignore_ips.length === 1 ? 'y' : 'ies'}.`, 'info');
                this.refresh();
            }
        });
        this.root.querySelectorAll('.fw-save-jail').forEach(btn => {
            btn.addEventListener('click', async () => {
                const row = btn.closest('tr');
                const data = { enabled: row.querySelector('.fw-jail-enabled').checked };
                row.querySelectorAll('.fw-jail-input').forEach(inp => { data[inp.dataset.key] = parseInt(inp.value, 10); });
                const r = await this.api('PUT', `/api/firewall/jails/${encodeURIComponent(row.dataset.jail)}`, data);
                if (this.handleResult(r)) this.refresh();
            });
        });
    }

    // ========================
    // 4. BANNED IPs (REAL-TIME)
    // ========================

    formatRemaining(seconds) {
        if (seconds === null || seconds === undefined) return '<span class="fw-target fw-target-drop">PERMANENT</span>';
        if (seconds <= 0) return 'expiring…';
        const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600);
        const m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
        const pad = (n) => String(n).padStart(2, '0');
        return (d ? `${d}d ` : '') + `${pad(h)}:${pad(m)}:${pad(s)}`;
    }

    bansTableHtml() {
        const bans = this.state.bans;
        if (!bans.length) return '<div class="settings-empty">No banned IP addresses.</div>';
        const fmt = (ts) => new Date(ts * 1000).toLocaleString();
        const rows = bans.map(b => `
            <tr>
                <td class="fw-mono"><strong>${this.esc(b.ip)}</strong></td>
                <td><span class="fw-badge">${this.esc(b.jail)}</span></td>
                <td>${this.esc(String(b.failures || '-'))}</td>
                <td>${this.esc(fmt(b.banned_at))}</td>
                <td class="fw-countdown" data-expires="${b.expires_at || ''}">${this.formatRemaining(b.remaining_seconds)}</td>
                <td class="fw-comment" title="${this.attr(b.reason)}">${this.esc(b.reason)}${b.actor && b.actor !== 'fail2ban' ? ` <span class="fw-dim">by ${this.esc(b.actor)}</span>` : ''}</td>
                <td class="fw-actions">
                    <button type="button" class="fw-unban" data-ip="${this.attr(b.ip)}" title="Unban">Unban</button>
                    <button type="button" class="fw-whitelist" data-ip="${this.attr(b.ip)}" title="Unban and add to whitelist">Whitelist</button>
                </td>
            </tr>`).join('');
        return `
            <table class="server-table fw-table">
                <thead><tr><th>IP Address</th><th>Jail</th><th>Failures</th><th>Banned At</th><th>Expires In</th><th>Reason</th><th></th></tr></thead>
                <tbody>${rows}</tbody>
            </table>`;
    }

    bansHtml() {
        return `
            <form class="fw-form" id="fwBanForm">
                <div class="fw-form-title">Manual ban</div>
                <div class="form-field"><label>IP Address</label><input name="ip" placeholder="203.0.113.10" required></div>
                <div class="form-field"><label>Duration (min, -1 = permanent)</label><input name="duration" type="number" min="-1" value="60" required></div>
                <div class="form-field fw-grow"><label>Reason</label><input name="reason" maxlength="200" placeholder="Suspicious activity"></div>
                <div class="fw-form-actions"><button type="submit">BAN IP</button></div>
            </form>
            <div class="fw-toolbar">
                <span class="settings-hint">Auto-refresh every 5 s.</span>
                <button type="button" id="fwRefreshBans"><svg class="md-icon"><use href="#md-refresh"></use></svg> Refresh</button>
            </div>
            <div class="server-table-wrapper" id="fwBansTable">${this.bansTableHtml()}</div>
        `;
    }

    bindBans() {
        this.root.querySelector('#fwRefreshBans').addEventListener('click', () => this.refreshBans());
        const form = this.root.querySelector('#fwBanForm');
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const data = Object.fromEntries(new FormData(form).entries());
            data.duration = parseInt(data.duration, 10);
            const r = await this.api('POST', '/api/firewall/bans', data);
            if (this.handleResult(r)) {
                form.reset();
                this.refreshBans();
            }
        });
        this.bindBanActions();
    }

    bindBanActions() {
        this.root.querySelectorAll('.fw-unban').forEach(btn => {
            btn.addEventListener('click', async () => {
                const r = await this.api('DELETE', '/api/firewall/bans', { ip: btn.dataset.ip });
                this.handleResult(r);
                this.refreshBans();
            });
        });
        this.root.querySelectorAll('.fw-whitelist').forEach(btn => {
            btn.addEventListener('click', () => {
                this.confirm('Add to Whitelist', `Unban ${btn.dataset.ip} and exempt it permanently from automatic bans?`, async () => {
                    const r = await this.api('POST', '/api/firewall/bans/whitelist', { ip: btn.dataset.ip });
                    this.handleResult(r);
                    this.refresh();
                });
            });
        });
    }

    tickCountdowns() {
        if (this.view !== 'bans') return;
        const now = Date.now() / 1000;
        this.root.querySelectorAll('.fw-countdown').forEach(td => {
            const exp = parseFloat(td.dataset.expires);
            if (exp) td.innerHTML = this.formatRemaining(Math.max(0, Math.round(exp - now)));
        });
    }

    updateBanCounter() {
        const c = this.root.querySelector('#fwBanCount');
        if (c && this.state) c.textContent = this.state.bans.length;
    }

    // ========================
    // UTILITIES
    // ========================

    async api(method, url, body = null) {
        const opts = { method, headers: { 'Content-Type': 'application/json' } };
        if (body) opts.body = JSON.stringify(body);
        try {
            const resp = await fetch(url, opts);
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok && !data.error) data.error = `Request failed (HTTP ${resp.status})`;
            return data;
        } catch (e) {
            return { error: `Network error: ${e.message}` };
        }
    }

    handleResult(res) {
        if (res && res.error) {
            this.alert(res.lockout_prevented ? 'Anti-Lockout Safeguard' : 'Firewall', res.error);
            return false;
        }
        return true;
    }

    // AmikoAlert/AmikoConfirm render their message as HTML: always pass escaped text
    alert(title, message, type = 'error') {
        if (window.AmikoAlert) window.AmikoAlert(this.esc(title), this.esc(message), type);
        else alert(message);
    }

    confirm(title, message, onConfirm) {
        if (window.AmikoConfirm) window.AmikoConfirm(this.esc(title), this.esc(message), onConfirm);
        else if (confirm(message)) onConfirm();
    }

    esc(str) {
        if (str === null || str === undefined) return '';
        const div = document.createElement('div');
        div.textContent = String(str);
        return div.innerHTML;
    }

    attr(str) {
        if (str === null || str === undefined) return '';
        return String(str).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const root = document.getElementById('firewallPanel');
    if (root) window.amikoFirewall = new FirewallPanel(root);
});
