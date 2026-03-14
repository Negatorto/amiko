/*
 * AMIKO - Settings Panel Logic
 * Full CRUD for Groups, Servers (Excel-like), and Themes
 */

class SettingsPanel {
    constructor() {
        this.overlay = document.getElementById('settingsOverlay');
        this.currentTab = 'groups';

        this.initTabs();
        this.initCloseBtn();
        this.initCrtToggle();
    }

    // --- Panel Open/Close ---

    open() {
        this.overlay.classList.add('active');
        this.switchTab(this.currentTab);
    }

    close() {
        this.overlay.classList.remove('active');
    }

    // --- Tab Navigation ---

    initTabs() {
        this.overlay.querySelectorAll('.settings-tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                this.switchTab(btn.dataset.tab);
            });
        });
    }

    switchTab(tabName) {
        this.currentTab = tabName;

        this.overlay.querySelectorAll('.settings-tab-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tab === tabName);
        });

        this.overlay.querySelectorAll('.settings-section').forEach(sec => {
            sec.classList.toggle('active', sec.dataset.section === tabName);
        });

        if (tabName === 'groups') this.loadGroups();
        else if (tabName === 'servers') this.loadServers();
        else if (tabName === 'themes') this.loadThemes();
        else if (tabName === 'workspace') { /* Loading handled globally by drag-drop.js */ }
    }

    initCloseBtn() {
        this.overlay.querySelector('.settings-close-btn').addEventListener('click', () => {
            this.close();
        });

        this.overlay.addEventListener('click', (e) => {
            if (e.target === this.overlay) this.close();
        });
    }

    // ========================
    // GROUPS TAB
    // ========================

    async loadGroups() {
        try {
            const groups = await this.api('GET', '/api/groups');
            const servers = await this.api('GET', '/api/servers');
            this.renderGroups(groups, servers);
        } catch (e) {
            console.error('[Settings] Error loading groups:', e);
        }
    }

    renderGroups(groups, servers) {
        const container = this.overlay.querySelector('.group-list');
        const form = this.overlay.querySelector('.group-form');

        // Form submit handler (re-bind)
        const newForm = form.cloneNode(true);
        form.parentNode.replaceChild(newForm, form);
        newForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const nameInput = newForm.querySelector('.group-name-input');
            const iconInput = newForm.querySelector('.group-icon-input');
            const name = nameInput.value.trim();
            const icon = iconInput.value.trim() || '📁';
            if (!name) return;
            await this.api('POST', '/api/groups', { name, icon });
            nameInput.value = '';
            iconInput.value = '';
            this.loadGroups();
        });

        if (groups.length === 0) {
            container.innerHTML = '<div class="settings-empty">No groups created yet. Create your first one above! ☝️</div>';
            return;
        }

        // Count servers per group
        const countMap = {};
        servers.forEach(s => {
            countMap[s.group_id] = (countMap[s.group_id] || 0) + 1;
        });

        container.innerHTML = groups.map(g => `
            <div class="group-card" data-id="${g.id}">
                <div class="group-card-info">
                    <span class="group-card-icon">${this.escapeHtml(g.icon)}</span>
                    <span class="group-card-name">${this.escapeHtml(g.name)}</span>
                    <span class="group-card-count">(${countMap[g.id] || 0} servers)</span>
                </div>
                <div class="group-card-actions">
                    <button class="btn-edit" title="Edit" data-id="${g.id}" data-name="${this.escapeAttr(g.name)}" data-icon="${this.escapeAttr(g.icon)}">✏️</button>
                    <button class="btn-delete" title="Delete" data-id="${g.id}">🗑️</button>
                </div>
            </div>
        `).join('');

        // Event delegation
        container.querySelectorAll('.btn-edit').forEach(btn => {
            btn.addEventListener('click', () => this.editGroup(btn.dataset.id, btn.dataset.name, btn.dataset.icon));
        });
        container.querySelectorAll('.btn-delete').forEach(btn => {
            btn.addEventListener('click', () => this.deleteGroup(btn.dataset.id));
        });
    }

    async editGroup(id, currentName, currentIcon) {
        const doEdit = async (newName, newIcon) => {
            await this.api('PUT', `/api/groups/${id}`, { name: newName, icon: newIcon });
            this.loadGroups();
        };

        if (window.AmikoPrompt) {
            window.AmikoPrompt('Edit Group', 'Group name:', currentName, (newName) => {
                if (newName.trim() === '') return;
                window.AmikoPrompt('Edit Group', 'Icon (emoji):', currentIcon, (newIcon) => {
                    doEdit(newName, newIcon || '📁');
                });
            });
        } else {
            const newName = prompt('Group name:', currentName);
            if (newName === null || newName.trim() === '') return;
            const newIcon = prompt('Icon (emoji):', currentIcon);
            if (newIcon === null) return;
            doEdit(newName, newIcon || '📁');
        }
    }

    async deleteGroup(id) {
        const doDelete = async () => {
            await this.api('DELETE', `/api/groups/${id}`);
            this.loadGroups();
        };
        if (window.AmikoConfirm) {
            window.AmikoConfirm('Delete Group', 'Delete this group and all associated servers?', doDelete);
        } else if (confirm('Delete this group and all associated servers?')) {
            doDelete();
        }
    }

    // ========================
    // SERVERS TAB (Excel-like)
    // ========================

    async loadServers() {
        try {
            const [groups, servers] = await Promise.all([
                this.api('GET', '/api/groups'),
                this.api('GET', '/api/servers')
            ]);
            this.renderServers(groups, servers);
        } catch (e) {
            console.error('[Settings] Error loading servers:', e);
        }
    }

    renderServers(groups, servers) {
        const tableWrapper = this.overlay.querySelector('.server-table-wrapper');
        const addRow = this.overlay.querySelector('.add-server-row');

        if (groups.length === 0) {
            tableWrapper.innerHTML = '<div class="settings-empty">Create a group first in the "Groups" tab.</div>';
            addRow.style.display = 'none';
            return;
        }

        addRow.style.display = 'flex';

        // Populate group select in add-row
        const groupSelect = addRow.querySelector('.field-group');
        if (groupSelect) {
            groupSelect.innerHTML = groups.map(g =>
                `<option value="${g.id}">${this.escapeHtml(g.icon)} ${this.escapeHtml(g.name)}</option>`
            ).join('');
        }

        // Build table
        const serversByGroup = {};
        groups.forEach(g => { serversByGroup[g.id] = []; });
        servers.forEach(s => {
            if (serversByGroup[s.group_id]) {
                serversByGroup[s.group_id].push(s);
            }
        });

        let tbody = '';
        groups.forEach(g => {
            const groupServers = serversByGroup[g.id] || [];
            tbody += `<tr class="group-row"><td colspan="8">${this.escapeHtml(g.icon)} ${this.escapeHtml(g.name)} (${groupServers.length})</td></tr>`;

            if (groupServers.length === 0) {
                tbody += `<tr><td colspan="8" style="text-align:center; color:var(--text-dim); font-style:italic;">No servers in this group</td></tr>`;
            }

            groupServers.forEach(s => {
                tbody += `
                    <tr data-server-id="${s.id}">
                        <td><span class="cell-editable" contenteditable="true" data-field="name">${this.escapeHtml(s.name)}</span></td>
                        <td><span class="cell-editable" contenteditable="true" data-field="hostname">${this.escapeHtml(s.hostname)}</span></td>
                        <td><span class="cell-editable" contenteditable="true" data-field="port">${s.port}</span></td>
                        <td><span class="cell-editable" contenteditable="true" data-field="username">${this.escapeHtml(s.username)}</span></td>
                        <td><span class="cell-editable cell-password" contenteditable="true" data-field="password" data-real-value="${this.escapeAttr(s.password)}">${'•'.repeat(s.password ? s.password.length : 0)}</span></td>
                        <td><span class="cell-editable" contenteditable="true" data-field="notes">${this.escapeHtml(s.notes || '')}</span></td>
                        <td>
                            <div class="row-actions">
                                <button class="btn-save" title="Save changes" data-id="${s.id}">💾</button>
                                <button class="btn-use" title="Use for connection" data-id="${s.id}">🔌</button>
                                <button class="btn-delete" title="Delete" data-id="${s.id}">🗑️</button>
                            </div>
                        </td>
                    </tr>
                `;
            });
        });

        tableWrapper.innerHTML = `
            <table class="server-table">
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>Hostname</th>
                        <th>Port</th>
                        <th>User</th>
                        <th>Password</th>
                        <th>Notes</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>${tbody}</tbody>
            </table>
        `;

        // Password reveal/hide on focus
        tableWrapper.querySelectorAll('.cell-password').forEach(cell => {
            cell.addEventListener('focus', () => {
                const real = cell.dataset.realValue || '';
                cell.textContent = real;
            });
            cell.addEventListener('blur', () => {
                const current = cell.textContent.trim();
                cell.dataset.realValue = current;
                cell.textContent = '•'.repeat(current.length);
            });
        });

        // Event delegation for table actions
        tableWrapper.addEventListener('click', (e) => {
            const btnSave = e.target.closest('.btn-save');
            const btnUse = e.target.closest('.btn-use');
            const btnDelete = e.target.closest('.btn-delete');

            if (btnSave) this.saveServerRow(btnSave.dataset.id);
            else if (btnUse) this.useServer(btnUse.dataset.id);
            else if (btnDelete) this.deleteServer(btnDelete.dataset.id);
        });

        // Add-server form handler (re-bind)
        const addForm = addRow.querySelector('.add-server-form');
        if (addForm) {
            const newForm = addForm.cloneNode(true);
            addForm.parentNode.replaceChild(newForm, addForm);
            newForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                const data = {
                    group_id: newForm.querySelector('.field-group').value,
                    name: newForm.querySelector('.field-name').value.trim(),
                    hostname: newForm.querySelector('.field-host').value.trim(),
                    port: parseInt(newForm.querySelector('.field-port').value) || 22,
                    username: newForm.querySelector('.field-user').value.trim() || 'root',
                    password: newForm.querySelector('.field-pass').value,
                    notes: ''
                };
                if (!data.name || !data.hostname) {
                    if (window.AmikoAlert) window.AmikoAlert('Error', 'Name and Hostname are required', 'warning');
                    else alert('Name and Hostname are required');
                    return;
                }
                await this.api('POST', '/api/servers', data);
                newForm.querySelector('.field-name').value = '';
                newForm.querySelector('.field-host').value = '';
                newForm.querySelector('.field-port').value = '22';
                newForm.querySelector('.field-user').value = '';
                newForm.querySelector('.field-pass').value = '';
                this.loadServers();
                this.refreshQuickList();
            });
        }
    }

    async saveServerRow(serverId) {
        const row = this.overlay.querySelector(`tr[data-server-id="${serverId}"]`);
        if (!row) return;

        const data = {};
        row.querySelectorAll('.cell-editable').forEach(cell => {
            if (cell.dataset.field === 'password') {
                data[cell.dataset.field] = cell.dataset.realValue || cell.textContent.trim();
            } else {
                data[cell.dataset.field] = cell.textContent.trim();
            }
        });

        await this.api('PUT', `/api/servers/${serverId}`, data);
        this.refreshQuickList();

        // Visual feedback
        row.style.transition = 'background-color 0.3s';
        row.style.backgroundColor = 'var(--bg-tertiary)';
        setTimeout(() => { row.style.backgroundColor = ''; }, 800);
    }

    async useServer(serverId) {
        const row = this.overlay.querySelector(`tr[data-server-id="${serverId}"]`);
        if (!row) return;

        const fields = {};
        row.querySelectorAll('.cell-editable').forEach(cell => {
            if (cell.dataset.field === 'password') {
                fields[cell.dataset.field] = cell.dataset.realValue || cell.textContent.trim();
            } else {
                fields[cell.dataset.field] = cell.textContent.trim();
            }
        });

        // Fill the global connection form
        document.getElementById('global_hostname').value = fields.hostname || '';
        document.getElementById('global_port').value = fields.port || 22;
        document.getElementById('global_username').value = fields.username || '';
        document.getElementById('global_password').value = fields.password || '';

        this.close();
    }

    async deleteServer(serverId) {
        const doDelete = async () => {
            await this.api('DELETE', `/api/servers/${serverId}`);
            this.loadServers();
            this.refreshQuickList();
        };

        if (window.AmikoConfirm) {
            window.AmikoConfirm('Delete Server', 'Delete this server?', doDelete);
        } else if (confirm('Delete this server?')) {
            doDelete();
        }
    }

    // ========================
    // THEMES TAB
    // ========================

    // Theme metadata (quotes from CSS comments)
    themesMeta = {
        'amiko-theme': { label: 'AMIKO Base', quote: 'Elegance is not about being noticed, it is about being remembered.' },
        'amiko-matrix': { label: 'Matrix', quote: 'Follow the white rabbit.' },
        'amiko-cyberpunk': { label: 'Cyberpunk', quote: 'Wake up, Samurai.' },
        'amiko-batman': { label: 'Batman', quote: 'I am vengeance. I am the night.' },
        'amiko-barbie': { label: 'Barbie', quote: 'Come on Barbie, let\'s go party!' },
        'amiko-classic': { label: 'Classic', quote: '640K ought to be enough for anybody.' },
        'amiko-synthwave': { label: 'Synthwave', quote: 'The future is now.' },
        'amiko-pacman': { label: 'Pac-Man', quote: 'WAKA WAKA WAKA' }
    };

    getThemeMeta(themeId) {
        if (this.themesMeta[themeId]) return this.themesMeta[themeId];
        // Auto-generate label from ID: "amiko-my-cool-theme" → "My Cool Theme"
        const name = themeId.replace('amiko-', '').split('-').map(
            w => w.charAt(0).toUpperCase() + w.slice(1)
        ).join(' ');
        return { label: name, quote: '' };
    }

    async loadThemes() {
        try {
            const data = await this.api('GET', '/api/themes');
            this.renderThemes(data);
            // Sync CRT toggle state from server
            const crtCheckbox = document.getElementById('crtToggle');
            if (crtCheckbox) {
                crtCheckbox.checked = data.crt_enabled;
                this.applyCrt(data.crt_enabled);
            }
        } catch (e) {
            console.error('[Settings] Error loading themes:', e);
        }
    }

    renderThemes(data) {
        const grid = this.overlay.querySelector('.themes-grid');

        grid.innerHTML = data.themes.map(t => {
            const meta = this.getThemeMeta(t.id);
            const classes = [
                'theme-card',
                t.active ? 'is-active' : ''
            ].filter(Boolean).join(' ');

            return `
                <div class="${classes}" data-theme-id="${t.id}">
                    <div class="theme-preview" id="preview-${t.id}"></div>
                    <div class="theme-card-body">
                        <div class="theme-card-info">
                            <span class="theme-card-name">${this.escapeHtml(meta.label)}</span>
                            ${t.active ? '<span class="badge-active">Active</span>' : ''}
                            <div class="theme-card-quote">"${this.escapeHtml(meta.quote)}"</div>
                        </div>
                        <div class="theme-card-actions">
                            <label class="theme-toggle-label">
                                <input type="checkbox" class="theme-toggle-checkbox" 
                                       data-theme="${t.id}" 
                                       ${t.active ? 'checked disabled' : ''}>
                            </label>
                        </div>
                    </div>
                </div>
            `;
        }).join('');

        // Load color previews
        data.themes.forEach(t => {
            this.loadThemePreview(t.id);
        });

        // Event delegation — checking a checkbox activates that theme
        grid.querySelectorAll('.theme-toggle-checkbox').forEach(chk => {
            if (!chk.disabled) {
                chk.addEventListener('change', () => this.activateTheme(chk.dataset.theme));
            }
        });
    }

    async loadThemePreview(themeId) {
        const previewEl = document.getElementById(`preview-${themeId}`);
        if (!previewEl) return;

        try {
            const response = await fetch(`/static/css/themes/${themeId}.css`);
            const cssText = await response.text();

            // Parse CSS variables
            const vars = {};
            const regex = /--([a-z-]+)\s*:\s*([^;]+)/g;
            let match;
            while ((match = regex.exec(cssText)) !== null) {
                vars[match[1]] = match[2].trim();
            }

            const colors = [
                vars['bg-primary'] || '#000',
                vars['bg-secondary'] || '#111',
                vars['text-primary'] || '#0f0',
                vars['text-secondary'] || '#0c0',
                vars['accent-warning'] || '#fa0',
                vars['accent-danger'] || '#f33',
                vars['accent-info'] || '#0af'
            ];

            previewEl.innerHTML = colors.map(c =>
                `<div class="theme-preview-swatch" style="background-color:${c}"></div>`
            ).join('');
        } catch (e) {
            previewEl.innerHTML = '<div style="padding:10px;color:var(--text-dim)">Preview not available</div>';
        }
    }

    async activateTheme(themeId) {
        const result = await this.api('PUT', '/api/themes/active', { theme: themeId });
        if (result.success) {
            this.applyTheme(themeId);
        }
        this.loadThemes();
    }

    async toggleTheme(themeId) {
        await this.api('PUT', `/api/themes/${themeId}/toggle`);
        this.loadThemes();
    }

    // --- CRT toggle (bound once in constructor) ---

    initCrtToggle() {
        const crtCheckbox = document.getElementById('crtToggle');
        if (crtCheckbox) {
            crtCheckbox.addEventListener('change', () => {
                this.toggleCrt(crtCheckbox.checked);
            });
        }
    }

    async toggleCrt(enabled) {
        await this.api('PUT', '/api/settings/crt', { enabled });
        this.applyCrt(enabled);
    }

    applyCrt(enabled) {
        document.body.classList.toggle('no-crt', !enabled);
    }

    applyTheme(themeId) {
        const link = document.getElementById('theme-stylesheet');
        if (link) {
            link.href = `/static/css/themes/${themeId}.css`;
            // Refresh JS theme bridge after CSS loads
            link.onload = () => {
                if (typeof refreshThemeVars === 'function') {
                    refreshThemeVars();
                }
            };
        }
    }

    // ========================
    // UTILITIES
    // ========================

    async api(method, url, body = null) {
        const opts = {
            method,
            headers: { 'Content-Type': 'application/json' }
        };
        if (body) opts.body = JSON.stringify(body);
        const resp = await fetch(url, opts);
        return resp.json();
    }

    escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = String(str);
        return div.innerHTML;
    }

    escapeAttr(str) {
        if (!str) return '';
        return String(str).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    refreshQuickList() {
        if (typeof loadSavedServers === 'function') {
            loadSavedServers();
        }
    }
}

// --- Boot ---
let settingsPanel;

document.addEventListener('DOMContentLoaded', () => {
    settingsPanel = new SettingsPanel();

    const settingsBtn = document.getElementById('btnSettings');
    if (settingsBtn) {
        settingsBtn.addEventListener('click', () => settingsPanel.open());
    }

    // Load initial theme from server
    fetch('/api/themes')
        .then(r => r.json())
        .then(data => {
            if (data.active_theme) {
                const link = document.getElementById('theme-stylesheet');
                if (link) {
                    link.href = `/static/css/themes/${data.active_theme}.css`;
                    link.onload = () => {
                        if (typeof refreshThemeVars === 'function') {
                            refreshThemeVars();
                        }
                    };
                }
            }
            // Apply CRT state on boot
            document.body.classList.toggle('no-crt', !data.crt_enabled);
        })
        .catch(() => { });
});
