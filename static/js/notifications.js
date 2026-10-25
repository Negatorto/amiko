/*
 * AMIKO - Email Notifications Settings Panel (Admin only)
 * SMTP configuration, notification categories, test email and delivery history.
 */

class NotificationsPanel {
    constructor(root) {
        this.root = root;
    }

    async load() {
        const res = await this.api('GET', '/api/notifications');
        if (res.error) {
            this.root.innerHTML = `<div class="settings-empty">${this.esc(res.error)}</div>`;
            return;
        }
        this.render(res.config, res.history || []);
    }

    render(cfg, history) {
        const cat = cfg.categories || {};
        const historyRows = history.map(h => `
            <tr>
                <td style="white-space:nowrap;">${this.esc(new Date(h.ts * 1000).toLocaleString())}</td>
                <td><span class="fw-target ${h.status === 'SENT' ? 'fw-target-accept' : 'fw-target-drop'}">${this.esc(h.status)}</span></td>
                <td class="fw-comment" style="max-width:420px;" title="${this.attr(h.subject)}">${this.esc(h.subject)}</td>
                <td class="fw-comment" title="${this.attr(h.error)}">${this.esc(h.error)}</td>
            </tr>`).join('');

        this.root.innerHTML = `
            <p class="settings-hint">Security events from the audit trail are emailed to a single security contact (e.g. CISO / SOC mailbox).
                <strong>Critical</strong> events are sent immediately; repeated events of the same type are grouped into a digest after the cooldown.
                Changing these settings is always reported to the previous recipient.</p>

            <form class="fw-form" id="ntfForm" autocomplete="off">
                <div class="fw-form-title">
                    <label class="fw-switch">
                        <input type="checkbox" class="theme-toggle-checkbox" name="enabled" ${cfg.enabled ? 'checked' : ''}>
                        <span>EMAIL NOTIFICATIONS ${cfg.enabled ? 'ACTIVE' : 'DISABLED'}</span>
                    </label>
                </div>
                <div class="form-field fw-grow"><label>SMTP Host</label><input name="host" value="${this.attr(cfg.host)}" placeholder="smtp.example.com"></div>
                <div class="form-field"><label>Port</label><input name="port" type="number" min="1" max="65535" value="${this.attr(cfg.port)}" style="min-width:80px; width:90px;"></div>
                <div class="form-field"><label>Security</label>
                    <select name="security">
                        <option value="starttls" ${cfg.security === 'starttls' ? 'selected' : ''}>STARTTLS (587)</option>
                        <option value="ssl" ${cfg.security === 'ssl' ? 'selected' : ''}>SSL/TLS (465)</option>
                    </select>
                </div>
                <div class="form-field"><label>Username</label><input name="username" value="${this.attr(cfg.username)}" autocomplete="off"></div>
                <div class="form-field"><label>Password</label>
                    <input name="password" type="password" autocomplete="new-password" placeholder="${cfg.has_password ? '•••••• (unchanged)' : 'not set'}">
                </div>
                <div class="form-field fw-grow"><label>Sender (From)</label><input name="from_addr" value="${this.attr(cfg.from_addr)}" placeholder="amiko@example.com"></div>
                <div class="form-field fw-grow"><label>Recipient (security contact)</label><input name="recipient" value="${this.attr(cfg.recipient)}" placeholder="security@example.com"></div>

                <div class="fw-form-title" style="margin-top:6px;">Categories</div>
                <label class="user-group-chip" title="SSH host key mismatch, audit chain broken, recidive bans, break-glass CLI actions"><input type="checkbox" name="cat_critical" ${cat.critical ? 'checked' : ''}><span>Critical (immediate)</span></label>
                <label class="user-group-chip" title="Fail2Ban bans, login lockouts, CSRF/CSWSH blocks, firewall lockout/egress blocks"><input type="checkbox" name="cat_high" ${cat.high ? 'checked' : ''}><span>Attacks &amp; blocks</span></label>
                <label class="user-group-chip" title="Users, 2FA resets, recovery codes, backup restore, firewall and Fail2Ban changes"><input type="checkbox" name="cat_changes" ${cat.changes ? 'checked' : ''}><span>Sensitive changes</span></label>
                <div class="form-field"><label>Digest cooldown (min)</label><input name="cooldown_min" type="number" min="1" max="1440" value="${this.attr(cfg.cooldown_min)}" style="min-width:80px; width:90px;"></div>

                <div class="fw-form-actions">
                    ${cfg.has_password ? '<button type="button" id="ntfClearPw">Clear password</button>' : ''}
                    <button type="button" id="ntfTest">Send test email</button>
                    <button type="submit">SAVE</button>
                </div>
            </form>

            <h4 class="fw-subtitle">Recent deliveries</h4>
            <div class="server-table-wrapper">
                ${history.length ? `
                <table class="server-table fw-table">
                    <thead><tr><th>Time</th><th>Status</th><th>Subject</th><th>Error</th></tr></thead>
                    <tbody>${historyRows}</tbody>
                </table>` : '<div class="settings-empty">No notifications sent since the server started.</div>'}
            </div>
        `;
        this.bind();
    }

    collect(form) {
        const f = new FormData(form);
        const data = {
            enabled: form.querySelector('[name="enabled"]').checked,
            host: f.get('host'),
            port: parseInt(f.get('port'), 10),
            security: f.get('security'),
            username: f.get('username'),
            from_addr: f.get('from_addr'),
            recipient: f.get('recipient'),
            cooldown_min: parseInt(f.get('cooldown_min'), 10),
            categories: {
                critical: form.querySelector('[name="cat_critical"]').checked,
                high: form.querySelector('[name="cat_high"]').checked,
                changes: form.querySelector('[name="cat_changes"]').checked
            }
        };
        const pw = f.get('password');
        if (pw) data.password = pw;
        return data;
    }

    bind() {
        const form = this.root.querySelector('#ntfForm');

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const r = await this.api('PUT', '/api/notifications', this.collect(form));
            if (r.error) return this.alert('Notifications', r.error);
            this.alert('Notifications', 'Settings saved.', 'info');
            this.load();
        });

        this.root.querySelector('#ntfTest').addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            // Save first so the test uses what is on screen
            const saved = await this.api('PUT', '/api/notifications', this.collect(form));
            if (saved.error) return this.alert('Notifications', saved.error);
            btn.disabled = true;
            btn.textContent = 'Sending…';
            const r = await this.api('POST', '/api/notifications/test');
            btn.disabled = false;
            btn.textContent = 'Send test email';
            if (r.error) this.alert('Test email', r.error);
            else this.alert('Test email', `Test email sent to ${saved.recipient}.`, 'info');
            this.load();
        });

        const clearBtn = this.root.querySelector('#ntfClearPw');
        if (clearBtn) {
            clearBtn.addEventListener('click', async () => {
                const r = await this.api('PUT', '/api/notifications', { password: '__CLEAR__' });
                if (r.error) this.alert('Notifications', r.error);
                this.load();
            });
        }
    }

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

    // AmikoAlert renders its message as HTML: always pass escaped text
    alert(title, message, type = 'error') {
        if (window.AmikoAlert) window.AmikoAlert(this.esc(title), this.esc(message), type);
        else alert(message);
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
    const root = document.getElementById('notificationsPanel');
    if (root) window.amikoNotifications = new NotificationsPanel(root);
});
