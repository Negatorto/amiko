/*
 * AMIKO - Settings Panel Logic
 * Full CRUD for Groups, Servers (Excel-like), and Themes
 */

class SettingsPanel {
    constructor() {
        this.overlay = document.getElementById('settingsOverlay');
        const isAdmin = window.amikoCurrentUser && window.amikoCurrentUser.role === 'admin';
        this.currentTab = isAdmin ? 'groups' : 'themes';

        this.initTabs();
        this.initCloseBtn();
        this.initCrtToggle();
        this.initVitalsMode();
        this.initInterface();
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
        else if (tabName === 'users') this.loadUsers();
        else if (tabName === 'audit') this.loadAudit();
        else if (tabName === 'themes') this.loadThemes();
        else if (tabName === 'interface') this.loadInterface();
    }

    initCloseBtn() {
        this.overlay.querySelector('.settings-close-btn').addEventListener('click', () => {
            this.close();
        });

        this.overlay.addEventListener('click', (e) => {
            if (e.target === this.overlay) this.close();
        });

        const refreshAuditBtn = document.getElementById('btnRefreshAudit');
        const statusFilter = document.getElementById('auditStatusFilter');
        if (refreshAuditBtn) refreshAuditBtn.addEventListener('click', () => this.loadAudit());
        if (statusFilter) statusFilter.addEventListener('change', () => this.loadAudit());
    }

    // ========================
    // AUDIT LOG TAB (Admin)
    // ========================

    async loadAudit() {
        const wrapper = document.getElementById('auditTableWrapper');
        const badge = document.getElementById('auditChainBadge');
        const statusFilter = document.getElementById('auditStatusFilter');
        if (!wrapper) return;

        const status = statusFilter ? statusFilter.value : '';
        const url = `/api/audit?limit=150${status ? `&status=${encodeURIComponent(status)}` : ''}`;

        try {
            const res = await this.api('GET', url);
            const chain = res.chain || { valid: true, total_entries: 0 };
            const events = res.events || [];

            if (badge) {
                if (chain.valid) {
                    badge.textContent = `🔒 SHA-256 Hash Chain Verified (${chain.total_entries} records)`;
                    badge.style.borderColor = 'rgba(0, 180, 80, 0.5)';
                    badge.style.color = 'var(--text-primary)';
                } else {
                    badge.textContent = `⚠️ Chain Tampering Detected at line ${chain.corrupted_line}! (${chain.reason || ''})`;
                    badge.style.borderColor = 'var(--accent-danger)';
                    badge.style.color = 'var(--accent-danger)';
                }
            }

            if (events.length === 0) {
                wrapper.innerHTML = '<div class="settings-empty">No audit events recorded yet.</div>';
                return;
            }

            const rows = events.map(ev => {
                const ts = (ev.timestamp || '').replace('T', ' ').slice(0, 19);
                const actor = ev.actor ? `${ev.actor.username || 'anon'} (${ev.actor.source_ip || '-'})` : '-';
                const targetObj = ev.target || {};
                const target = targetObj.hostname
                    ? `${targetObj.hostname}:${targetObj.port || 22}${targetObj.resource ? ' ' + targetObj.resource : ''}`
                    : (targetObj.resource || '-');
                const detailsStr = ev.details && Object.keys(ev.details).length > 0
                    ? JSON.stringify(ev.details)
                    : '';
                const hashShort = (ev.integrity_hash || '').slice(0, 10);
                const statusColor = ev.status === 'SUCCESS'
                    ? '#2ecc71'
                    : (ev.status === 'DENIED' ? '#e74c3c' : '#f39c12');

                return `
                    <tr>
                        <td style="white-space:nowrap; font-size:11px;">${this.escapeHtml(ts)}</td>
                        <td style="font-weight:700; font-size:11px;">${this.escapeHtml(ev.event_type)}</td>
                        <td><span style="color:${statusColor}; font-weight:700; font-size:11px;">${this.escapeHtml(ev.status)}</span></td>
                        <td style="font-size:11px;">${this.escapeHtml(actor)}</td>
                        <td style="font-size:11px;">${this.escapeHtml(target)}</td>
                        <td style="font-size:11px; max-width:220px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${this.escapeAttr(detailsStr)}">${this.escapeHtml(detailsStr)}</td>
                        <td style="font-family:monospace; font-size:10px; color:var(--text-dim);" title="${this.escapeAttr(ev.integrity_hash || '')}">${this.escapeHtml(hashShort)}…</td>
                    </tr>
                `;
            }).join('');

            wrapper.innerHTML = `
                <table class="server-table">
                    <thead>
                        <tr>
                            <th>Timestamp (UTC)</th>
                            <th>Event</th>
                            <th>Status</th>
                            <th>Actor (IP)</th>
                            <th>Target</th>
                            <th>Details</th>
                            <th>SHA-256</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            `;
        } catch (e) {
            console.error('[Settings] Error loading audit log:', e);
        }
    }

    // ========================
    // USERS & RBAC TAB (Admin)
    // ========================

    async loadUsers() {
        try {
            const [users, groups] = await Promise.all([
                this.api('GET', '/api/users'),
                this.api('GET', '/api/groups')
            ]);
            this.renderUsers(users, groups);
        } catch (e) {
            console.error('[Settings] Error loading users:', e);
        }
    }

    renderUsers(users, groups) {
        const container = this.overlay.querySelector('.user-list');
        const form = this.overlay.querySelector('.user-form');
        if (!container || !form) return;

        // Re-bind form cleanly
        const newForm = form.cloneNode(true);
        form.parentNode.replaceChild(newForm, form);

        const roleSelect = newForm.querySelector('.user-role-select');
        const groupsField = newForm.querySelector('.user-groups-field');
        const checkboxesBox = newForm.querySelector('.user-groups-checkboxes');

        if (checkboxesBox) {
            if (groups.length === 0) {
                checkboxesBox.innerHTML = '<span style="font-size:12px; color:var(--text-dim);">Create groups first in the Groups tab</span>';
            } else {
                checkboxesBox.innerHTML = groups.map(g => `
                    <label class="user-group-chip">
                        <input type="checkbox" value="${g.id}" checked>
                        <span>${this.escapeHtml(g.icon)} ${this.escapeHtml(g.name)}</span>
                    </label>
                `).join('');
            }
        }

        const updateGroupVisibility = () => {
            if (groupsField && roleSelect) {
                groupsField.style.opacity = roleSelect.value === 'admin' ? '0.4' : '1';
                groupsField.style.pointerEvents = roleSelect.value === 'admin' ? 'none' : 'auto';
            }
        };
        if (roleSelect) {
            roleSelect.addEventListener('change', updateGroupVisibility);
            updateGroupVisibility();
        }

        newForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const username = newForm.querySelector('.user-username-input').value.trim();
            const password = newForm.querySelector('.user-password-input').value;
            const role = roleSelect.value;
            const allowed_groups = role === 'admin'
                ? ['*']
                : Array.from(newForm.querySelectorAll('.user-groups-checkboxes input:checked')).map(cb => cb.value);

            if (!username || !password) return;
            const res = await this.api('POST', '/api/users', { username, password, role, allowed_groups });
            if (res.error) {
                if (window.AmikoAlert) window.AmikoAlert('Error', res.error);
                else alert(res.error);
                return;
            }
            newForm.reset();
            updateGroupVisibility();
            this.loadUsers();
        });

        const groupMap = {};
        groups.forEach(g => { groupMap[g.id] = `${g.icon} ${g.name}`; });
        const currentUserId = window.amikoCurrentUser ? window.amikoCurrentUser.id : null;

        container.innerHTML = users.map(u => {
            const isSelf = u.id === currentUserId;
            const isAdmin = u.role === 'admin';
            const groupChips = isAdmin
                ? `<span class="user-assigned-chip all-groups">🌐 All Server Groups</span>`
                : (u.allowed_groups && u.allowed_groups.length > 0
                    ? u.allowed_groups.map(gid => `<span class="user-assigned-chip">${this.escapeHtml(groupMap[gid] || gid)}</span>`).join('')
                    : `<span class="user-assigned-chip no-groups">⚠️ No groups assigned</span>`);

            return `
                <div class="group-card user-card" data-user-id="${u.id}">
                    <div class="group-card-info" style="flex-wrap:wrap; gap:8px;">
                        <span class="group-card-name">👤 ${this.escapeHtml(u.username)} ${isSelf ? '<em style="font-size:11px; color:var(--text-dim);">(you)</em>' : ''}</span>
                        <span class="topbar-role-badge role-${u.role}">${u.role.toUpperCase()}</span>
                        <div class="user-assigned-groups">${groupChips}</div>
                    </div>
                    <div class="group-card-actions">
                        <button class="btn-edit-user" title="Edit Role & Assigned Groups" data-id="${u.id}">🛡️</button>
                        <button class="btn-pass-user" title="Reset Password" data-id="${u.id}" data-username="${this.escapeAttr(u.username)}">🔑</button>
                        ${!isSelf ? `<button class="btn-delete-user" title="Delete User" data-id="${u.id}" data-username="${this.escapeAttr(u.username)}">🗑️</button>` : ''}
                    </div>
                </div>
            `;
        }).join('');

        container.querySelectorAll('.btn-pass-user').forEach(btn => {
            btn.addEventListener('click', () => {
                const uid = btn.dataset.id;
                const uname = btn.dataset.username;
                const doReset = async (newPass) => {
                    if (!newPass || !newPass.trim()) return;
                    const r = await this.api('PUT', `/api/users/${uid}`, { password: newPass.trim() });
                    if (r.error && window.AmikoAlert) window.AmikoAlert('Error', r.error);
                    else if (window.AmikoAlert) window.AmikoAlert('Password Updated', `Password for ${uname} has been updated.`);
                };
                if (window.AmikoPrompt) {
                    window.AmikoPrompt('Reset Password', `New password for ${uname}:`, '', doReset);
                } else {
                    const p = prompt(`New password for ${uname}:`);
                    if (p) doReset(p);
                }
            });
        });

        container.querySelectorAll('.btn-edit-user').forEach(btn => {
            btn.addEventListener('click', () => {
                const user = users.find(u => u.id === btn.dataset.id);
                if (user) this.openEditUserInline(user, groups);
            });
        });

        container.querySelectorAll('.btn-delete-user').forEach(btn => {
            btn.addEventListener('click', () => {
                const uid = btn.dataset.id;
                const uname = btn.dataset.username;
                const doDel = async () => {
                    const r = await this.api('DELETE', `/api/users/${uid}`);
                    if (r.error && window.AmikoAlert) window.AmikoAlert('Error', r.error);
                    this.loadUsers();
                };
                if (window.AmikoConfirm) {
                    window.AmikoConfirm('Delete User', `Delete user "${uname}"?`, doDel);
                } else if (confirm(`Delete user "${uname}"?`)) {
                    doDel();
                }
            });
        });
    }

    openEditUserInline(user, groups) {
        const card = this.overlay.querySelector(`.user-card[data-user-id="${user.id}"]`);
        if (!card) return;

        const assignedSet = new Set(user.allowed_groups || []);
        card.innerHTML = `
            <div style="width:100%; display:flex; flex-direction:column; gap:10px;">
                <div style="display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap;">
                    <strong>Edit ${this.escapeHtml(user.username)}</strong>
                    <select class="inline-role-select" style="padding:5px 8px; background:var(--bg-primary); color:var(--text-primary); border:1px solid var(--border); font-family:var(--font-mono);">
                        <option value="operator" ${user.role === 'operator' ? 'selected' : ''}>Operator (R/W + SSH)</option>
                        <option value="viewer" ${user.role === 'viewer' ? 'selected' : ''}>Viewer (Read-Only, No SSH)</option>
                        <option value="admin" ${user.role === 'admin' ? 'selected' : ''}>Admin (Full Control)</option>
                    </select>
                </div>
                <div class="inline-groups-box" style="display:flex; flex-wrap:wrap; gap:6px; ${user.role === 'admin' ? 'opacity:0.4; pointer-events:none;' : ''}">
                    ${groups.map(g => `
                        <label class="user-group-chip">
                            <input type="checkbox" value="${g.id}" ${assignedSet.has(g.id) || assignedSet.has('*') ? 'checked' : ''}>
                            <span>${this.escapeHtml(g.icon)} ${this.escapeHtml(g.name)}</span>
                        </label>
                    `).join('')}
                </div>
                <div style="display:flex; justify-content:flex-end; gap:8px;">
                    <button type="button" class="btn-cancel-inline" style="padding:5px 10px;">Cancel</button>
                    <button type="button" class="btn-save-inline" style="padding:5px 12px; background:var(--accent-highlight); color:#fff; border:none; cursor:pointer;">💾 Save</button>
                </div>
            </div>
        `;

        const roleSel = card.querySelector('.inline-role-select');
        const gBox = card.querySelector('.inline-groups-box');
        roleSel.addEventListener('change', () => {
            gBox.style.opacity = roleSel.value === 'admin' ? '0.4' : '1';
            gBox.style.pointerEvents = roleSel.value === 'admin' ? 'none' : 'auto';
        });

        card.querySelector('.btn-cancel-inline').addEventListener('click', () => this.loadUsers());
        card.querySelector('.btn-save-inline').addEventListener('click', async () => {
            const newRole = roleSel.value;
            const newGroups = newRole === 'admin'
                ? ['*']
                : Array.from(card.querySelectorAll('.inline-groups-box input:checked')).map(cb => cb.value);
            const r = await this.api('PUT', `/api/users/${user.id}`, { role: newRole, allowed_groups: newGroups });
            if (r.error) {
                if (window.AmikoAlert) window.AmikoAlert('Error', r.error);
                else alert(r.error);
            }
            this.loadUsers();
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
                const hasPw = s.has_password !== false && Boolean(s.has_password || s.password);
                const hasKey = Boolean(s.has_private_key);
                tbody += `
                    <tr data-server-id="${s.id}" data-group-id="${this.escapeAttr(s.group_id || '')}">
                        <td><span class="cell-editable" contenteditable="true" data-field="name">${this.escapeHtml(s.name)}</span></td>
                        <td><span class="cell-editable" contenteditable="true" data-field="hostname">${this.escapeHtml(s.hostname)}</span></td>
                        <td><span class="cell-editable" contenteditable="true" data-field="port">${s.port}</span></td>
                        <td><span class="cell-editable" contenteditable="true" data-field="username">${this.escapeHtml(s.username)}</span></td>
                        <td><span class="cell-editable cell-password" contenteditable="true" data-field="password" data-has-password="${hasPw ? '1' : '0'}" data-real-value="" title="Click to enter a new password (leave blank to keep stored secret)">${hasKey ? '🔑 SSH Key' : (hasPw ? '••••••••' : '')}</span></td>
                        <td><span class="cell-editable" contenteditable="true" data-field="notes">${this.escapeHtml(s.notes || '')}</span></td>
                        <td>
                            <div class="row-actions">
                                <button class="btn-save" title="Save changes" data-id="${s.id}">💾</button>
                                <button class="btn-sshkey" title="Set SSH Private Key (Ed25519 / RSA)" data-id="${s.id}" data-name="${this.escapeAttr(s.name)}">🔑</button>
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

        // Password edit on focus (never exposes stored password; only sets a new one if typed)
        tableWrapper.querySelectorAll('.cell-password').forEach(cell => {
            cell.addEventListener('focus', () => {
                cell.textContent = cell.dataset.realValue || '';
            });
            cell.addEventListener('blur', () => {
                const current = cell.textContent.trim();
                cell.dataset.realValue = current;
                if (current) {
                    cell.textContent = '•'.repeat(current.length);
                } else {
                    cell.textContent = cell.dataset.hasPassword === '1' ? '••••••••' : '';
                }
            });
        });

        // Event delegation for table actions
        tableWrapper.addEventListener('click', (e) => {
            const btnSave = e.target.closest('.btn-save');
            const btnKey = e.target.closest('.btn-sshkey');
            const btnUse = e.target.closest('.btn-use');
            const btnDelete = e.target.closest('.btn-delete');

            if (btnSave) this.saveServerRow(btnSave.dataset.id);
            else if (btnKey) this.setServerSshKey(btnKey.dataset.id, btnKey.dataset.name);
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

        this.loadKnownHosts();
    }

    async loadKnownHosts() {
        const wrapper = this.overlay.querySelector('#knownHostsTableWrapper');
        if (!wrapper) return;

        const entries = await this.api('GET', '/api/known-hosts');
        if (!Array.isArray(entries) || entries.length === 0) {
            wrapper.innerHTML = `<div style="padding:12px; color:var(--text-dim); font-size:12px; font-style:italic;">No SSH host keys pinned yet. Keys are automatically pinned and verified on first connection.</div>`;
            return;
        }

        const rows = entries.map(item => `
            <tr>
                <td style="font-family:var(--font-mono); font-size:12px;">${this.escapeHtml(item.host || '')}</td>
                <td style="font-family:var(--font-mono); font-size:11px; color:var(--text-dim);">${this.escapeHtml(item.key_type || '')}</td>
                <td style="font-family:var(--font-mono); font-size:11px; color:var(--accent-highlight);">${this.escapeHtml(item.fingerprint || '')}</td>
                <td style="width:60px; text-align:center;">
                    <button class="btn-del-kh" title="Unpin / Reset Host Key" data-host="${this.escapeAttr(item.host || '')}" style="background:none; border:none; cursor:pointer;">🗑️</button>
                </td>
            </tr>
        `).join('');

        wrapper.innerHTML = `
            <table class="server-table">
                <thead>
                    <tr>
                        <th>Host Entry</th>
                        <th>Key Type</th>
                        <th>SHA-256 Fingerprint</th>
                        <th>Reset</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        `;

        wrapper.querySelectorAll('.btn-del-kh').forEach(btn => {
            btn.addEventListener('click', () => {
                const host = btn.dataset.host;
                const doRemove = async () => {
                    await this.api('DELETE', '/api/known-hosts', { host });
                    this.loadKnownHosts();
                };
                if (window.AmikoConfirm) {
                    window.AmikoConfirm('Reset SSH Host Key', `Remove pinned host key for "${host}"?`, doRemove);
                } else if (confirm(`Remove pinned host key for "${host}"?`)) {
                    doRemove();
                }
            });
        });
    }

    async saveServerRow(serverId) {
        const row = this.overlay.querySelector(`tr[data-server-id="${serverId}"]`);
        if (!row) return;

        const data = {};
        row.querySelectorAll('.cell-editable').forEach(cell => {
            if (cell.dataset.field === 'password') {
                const newPw = (cell.dataset.realValue || '').trim();
                if (newPw) {
                    data.password = newPw;
                    cell.dataset.hasPassword = '1';
                    cell.dataset.realValue = '';
                }
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

    async setServerSshKey(serverId, serverName) {
        const applyKey = async (pemValue) => {
            const cleaned = (pemValue || '').trim();
            const r = await this.api('PUT', `/api/servers/${serverId}`, { private_key: cleaned || '__CLEAR__' });
            if (r && r.error) {
                if (window.AmikoAlert) window.AmikoAlert('Error', r.error);
                return;
            }
            this.loadServers();
            this.refreshQuickList();
            if (window.AmikoAlert) {
                const msg = cleaned
                    ? `Encrypted SSH private key stored in vault for "${serverName}".`
                    : `SSH private key cleared for "${serverName}".`;
                window.AmikoAlert('SSH Key Updated', msg, 'info');
            }
        };

        if (window.WinBox) {
            const wb = new WinBox({
                title: `SSH Private Key — ${serverName}`,
                html: `
                    <div class="amiko-alert-content" style="padding: 16px; display:flex; flex-direction:column; gap:10px; height:100%; box-sizing:border-box;">
                        <p style="margin:0; font-size:12px; color: var(--text-primary); text-align:left;">
                            Paste the multi-line PEM / OpenSSH private key (<code>-----BEGIN OPENSSH PRIVATE KEY-----</code>) for <strong>${this.escapeHtml(serverName)}</strong>. Leave empty and click Save to remove a stored key.
                        </p>
                        <textarea id="amiko-sshkey-textarea" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;...&#10;-----END OPENSSH PRIVATE KEY-----" style="width:100%; flex:1; min-height:140px; padding:8px; background:var(--bg-tertiary); color:var(--text-primary); border:1px solid var(--border-medium); border-radius:4px; font-family:var(--font-mono); font-size:11px; resize:none;"></textarea>
                        <div style="display:flex; gap:10px; justify-content:flex-end;">
                            <button id="btn-sshkey-save" class="btn-connect" style="background: rgba(0, 180, 80, 0.15); color: #4cff88; border: 1px solid rgba(0, 180, 80, 0.4); padding: 6px 18px; border-radius: 4px; cursor: pointer;">Save Key</button>
                            <button id="btn-sshkey-cancel" class="btn-disconnect" style="background: rgba(255, 60, 60, 0.1); color: #ff6b6b; border: 1px solid rgba(255, 60, 60, 0.3); padding: 6px 18px; border-radius: 4px; cursor: pointer;">Cancel</button>
                        </div>
                    </div>
                `,
                class: ['wb-amiko', 'wb-amiko-warning'],
                width: 520,
                height: 310,
                x: "center",
                y: "center",
                top: 70,
                index: 10001,
                modal: true
            });
            setTimeout(() => {
                const ta = document.getElementById('amiko-sshkey-textarea');
                const btnSave = document.getElementById('btn-sshkey-save');
                const btnCancel = document.getElementById('btn-sshkey-cancel');
                if (ta) ta.focus();
                if (btnSave) btnSave.addEventListener('click', () => {
                    const val = ta ? ta.value : '';
                    wb.close();
                    applyKey(val);
                });
                if (btnCancel) btnCancel.addEventListener('click', () => wb.close());
            }, 80);
        } else {
            const pem = prompt(`Paste SSH private key (PEM/OpenSSH) for "${serverName}":`);
            if (pem !== null) applyKey(pem);
        }
    }

    async useServer(serverId) {
        const row = this.overlay.querySelector(`tr[data-server-id="${serverId}"]`);
        if (!row) return;

        const fields = {};
        row.querySelectorAll('.cell-editable').forEach(cell => {
            if (cell.dataset.field !== 'password') {
                fields[cell.dataset.field] = cell.textContent.trim();
            }
        });

        window.globalConnection.serverId = serverId;
        window.globalConnection.groupId = row.dataset.groupId || null;

        // Fill the global connection form (password remains in backend vault)
        document.getElementById('global_hostname').value = fields.hostname || '';
        document.getElementById('global_port').value = fields.port || 22;
        document.getElementById('global_username').value = fields.username || '';
        const pwInput = document.getElementById('global_password');
        if (pwInput) {
            pwInput.value = '';
            pwInput.placeholder = 'Stored in Vault 🔒';
            pwInput.required = false;
        }

        // Sync #saved_servers dropdown selection if present
        const savedSelect = document.getElementById('saved_servers');
        if (savedSelect) {
            for (const opt of savedSelect.options) {
                if (opt.value && opt.value !== 'new') {
                    try {
                        const parsed = JSON.parse(opt.value);
                        if (parsed.id === serverId) {
                            savedSelect.value = opt.value;
                            break;
                        }
                    } catch (_) {}
                }
            }
        }

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

    // --- Vitals Display Mode ---

    initVitalsMode() {
        const sel = document.getElementById('vitalsDisplayMode');
        if (!sel) return;

        // Restore saved preference
        const saved = localStorage.getItem('amiko_vitals_mode') || 'bars';
        sel.value = saved;
        this._applyVitalsMode(saved);

        sel.addEventListener('change', () => {
            this._applyVitalsMode(sel.value);
            localStorage.setItem('amiko_vitals_mode', sel.value);
        });
    }

    _applyVitalsMode(mode) {
        const barEl  = document.getElementById('resourceMonitorHeader');
        const textEl = document.getElementById('resourceMonitorText');
        // Only switch visibility when connected (both start hidden)
        const isConnected = window.globalConnection && window.globalConnection.connected;
        if (!isConnected) return;

        if (mode === 'text') {
            if (barEl)  barEl.classList.add('hidden');
            if (textEl) textEl.classList.remove('hidden');
        } else {
            if (textEl) textEl.classList.add('hidden');
            if (barEl)  barEl.classList.remove('hidden');
        }
    }

    // ========================
    // INTERFACE TAB
    // ========================

    initInterface() {
        // Font size slider live feedback
        const fontSlider = document.getElementById('uiFontSize');
        const fontVal    = document.getElementById('uiFontSizeValue');
        if (fontSlider && fontVal) {
            fontSlider.addEventListener('input', () => {
                fontVal.textContent = fontSlider.value + 'px';
                document.documentElement.style.fontSize = fontSlider.value + 'px';
            });
        }

        // Explorer max file size slider live feedback
        const slider    = document.getElementById('explorerMaxFileMb');
        const sliderVal = document.getElementById('explorerMaxFileMbValue');
        if (slider && sliderVal) {
            slider.addEventListener('input', () => {
                sliderVal.textContent = slider.value + ' MB';
            });
        }

        // Save button
        const saveBtn = document.getElementById('ifaceSaveBtn');
        if (saveBtn) {
            saveBtn.addEventListener('click', () => this.saveInterface());
        }

        // Optional Backup Export / Import (Admin)
        const btnExport = document.getElementById('btnExportBackup');
        const btnImport = document.getElementById('btnImportBackup');
        const fileInput = document.getElementById('backupFileInput');
        const passInput = document.getElementById('backupPassphrase');

        if (btnExport) {
            btnExport.addEventListener('click', async () => {
                const passphrase = passInput ? passInput.value.trim() : '';
                const res = await this.api('POST', '/api/backup/export', { passphrase });
                if (res && res.error) {
                    if (window.AmikoAlert) window.AmikoAlert('Backup Error', res.error);
                    return;
                }
                const blob = new Blob([JSON.stringify(res, null, 2)], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                const dateStr = new Date().toISOString().slice(0, 10);
                a.href = url;
                a.download = `amiko_backup_${dateStr}${res.encrypted ? '.enc.json' : '.json'}`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                URL.revokeObjectURL(url);
            });
        }

        if (btnImport && fileInput) {
            btnImport.addEventListener('click', () => fileInput.click());
            fileInput.addEventListener('change', async () => {
                const file = fileInput.files && fileInput.files[0];
                if (!file) return;
                try {
                    const text = await file.text();
                    const bundle = JSON.parse(text);
                    const passphrase = passInput ? passInput.value.trim() : '';
                    const res = await this.api('POST', '/api/backup/import', { bundle, passphrase });
                    fileInput.value = '';
                    if (res && res.error) {
                        if (window.AmikoAlert) window.AmikoAlert('Restore Error', res.error);
                        return;
                    }
                    this.refreshQuickList();
                    this.loadInterface();
                    if (window.AmikoAlert) {
                        window.AmikoAlert('Backup Restored', 'Configuration, servers, groups, users, and notes have been restored.', 'info');
                    }
                } catch (err) {
                    fileInput.value = '';
                    if (window.AmikoAlert) window.AmikoAlert('Restore Error', 'Invalid JSON backup file.');
                }
            });
        }
    }

    async loadInterface() {
        try {
            const prefs = await this.api('GET', '/api/settings/ui');
            this._applyInterfaceFormValues(prefs);
            this._applyModuleGuards(prefs.active_modules || []);
            this._applyUiPrefsToApp(prefs);
        } catch(e) {
            console.error('[Settings] Error loading interface prefs:', e);
        }
    }

    /**
     * Hide any .iface-pref-group[data-requires-module="X"] elements
     * where "X" is not in the activeModules list.
     * This makes module-specific settings disappear automatically
     * when the module folder is removed or disabled.
     */
    _applyModuleGuards(activeModules) {
        const section = this.overlay.querySelector('[data-section="interface"]');
        if (!section) return;

        const activeSet = new Set(activeModules);
        section.querySelectorAll('[data-requires-module]').forEach(el => {
            const required = el.dataset.requiresModule;
            el.style.display = activeSet.has(required) ? '' : 'none';
        });
    }


    _applyInterfaceFormValues(prefs) {
        // Font size
        const fontSlider = document.getElementById('uiFontSize');
        const fontVal    = document.getElementById('uiFontSizeValue');
        if (fontSlider) {
            fontSlider.value = prefs.ui_font_size ?? 13;
            if (fontVal) fontVal.textContent = fontSlider.value + 'px';
        }

        // Clock format
        const fmt = prefs.clock_format ?? '24h';
        const r24 = document.getElementById('clockFormat24');
        const r12 = document.getElementById('clockFormat12');
        if (r24) r24.checked = fmt === '24h';
        if (r12) r12.checked = fmt === '12h';

        // Animations
        const animChk = document.getElementById('animationsEnabled');
        if (animChk) animChk.checked = prefs.animations_enabled !== false;

        // WinBox buttons
        const btnMap = { min: 'wbBtnMin', max: 'wbBtnMax', full: 'wbBtnFull' };
        const active = new Set(prefs.winbox_buttons || ['min', 'max', 'full', 'close']);
        for (const [val, id] of Object.entries(btnMap)) {
            const chk = document.getElementById(id);
            if (chk) chk.checked = active.has(val);
        }

        // Explorer: max file size
        const slider    = document.getElementById('explorerMaxFileMb');
        const sliderVal = document.getElementById('explorerMaxFileMbValue');
        if (slider) {
            slider.value = prefs.explorer_max_file_mb ?? 10;
            if (sliderVal) sliderVal.textContent = slider.value + ' MB';
        }

        // Explorer: show hidden
        const showHidden = document.getElementById('explorerShowHidden');
        if (showHidden) showHidden.checked = !!prefs.explorer_show_hidden;

        // Explorer: confirm delete
        const confirmDel = document.getElementById('explorerConfirmDelete');
        if (confirmDel) confirmDel.checked = prefs.explorer_confirm_delete !== false;
    }

    async saveInterface() {
        // General
        const ui_font_size = parseInt(document.getElementById('uiFontSize')?.value ?? 13);
        const clock_format = document.querySelector('input[name="clockFormat"]:checked')?.value ?? '24h';
        const animations_enabled = document.getElementById('animationsEnabled')?.checked !== false;

        // WinBox buttons
        const buttons = ['close'];
        const btnMap  = { min: 'wbBtnMin', max: 'wbBtnMax', full: 'wbBtnFull' };
        for (const [val, id] of Object.entries(btnMap)) {
            const chk = document.getElementById(id);
            if (chk && chk.checked) buttons.push(val);
        }

        // Explorer
        const explorer_max_file_mb  = parseInt(document.getElementById('explorerMaxFileMb')?.value ?? 10);
        const explorer_show_hidden  = document.getElementById('explorerShowHidden')?.checked ?? false;
        const explorer_confirm_delete = document.getElementById('explorerConfirmDelete')?.checked !== false;

        try {
            const result = await this.api('PUT', '/api/settings/ui', {
                ui_font_size,
                clock_format,
                animations_enabled,
                winbox_buttons: buttons,
                explorer_max_file_mb,
                explorer_show_hidden,
                explorer_confirm_delete
            });
            window.amikoUiPrefs = result;
            this._applyUiPrefsToApp(result);
            // Visual feedback
            const btn = document.getElementById('ifaceSaveBtn');
            if (btn) {
                const orig = btn.textContent;
                btn.textContent = '✅ Saved!';
                btn.disabled = true;
                setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 1800);
            }
        } catch(e) {
            if (window.AmikoAlert) window.AmikoAlert('Error', 'Could not save interface settings.', 'error');
        }
    }

    /**
     * Apply UI prefs immediately to the live document (no reload needed).
     * Called on boot, on tab open, and after save.
     *
     * Font scaling strategy:
     *  - html font-size  → controls rem units (UI chrome, settings panel, etc.)
     *  - --font-size-base / --font-size-small / --font-size-large → override theme vars
     *  - --content-font-size → used by .amiko-module content (base + 1px for readability)
     */
    _applyUiPrefsToApp(prefs) {
        const fs = prefs.ui_font_size ?? 13;  // chosen by slider

        // 1. Scale rem-based UI chrome
        document.documentElement.style.fontSize = fs + 'px';

        // 2. Override theme CSS font-size variables so px-based rules scale too
        //    base = selected value, small = base-1, large = base+2
        const root = document.documentElement;
        root.style.setProperty('--font-size-base',  fs + 'px');
        root.style.setProperty('--font-size-small', (fs - 1) + 'px');
        root.style.setProperty('--font-size-large', (fs + 2) + 'px');

        // 3. Module content: slightly bigger for readability (base + 1px)
        //    Modules use var(--content-font-size) — apply it to .amiko-module wrappers
        root.style.setProperty('--content-font-size', (fs + 1) + 'px');

        // 4. Animations → toggle class on <html>
        root.classList.toggle('no-animations', !prefs.animations_enabled);

        // 5. Clock format → force immediate update
        if (window.AmikoDragDrop && typeof window.AmikoDragDrop._updateClock === 'function') {
            window.AmikoDragDrop._updateClock();
        }
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

    const logoutBtn = document.getElementById('btnLogout');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', async () => {
            try {
                if (typeof disconnectGlobal === 'function') disconnectGlobal();
                await fetch('/api/auth/logout', { method: 'POST' });
            } catch (_) {}
            sessionStorage.removeItem('globalConnection');
            window.location.href = '/login';
        });
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

    // Load UI prefs so drag-drop.js and explorer can read them from boot
    fetch('/api/settings/ui')
        .then(r => r.json())
        .then(prefs => {
            window.amikoUiPrefs = prefs;
            // Apply immediately: font size, CSS vars, animations
            const fs   = prefs.ui_font_size ?? 13;
            const root = document.documentElement;
            root.style.fontSize = fs + 'px';
            root.style.setProperty('--font-size-base',     fs + 'px');
            root.style.setProperty('--font-size-small',    (fs - 1) + 'px');
            root.style.setProperty('--font-size-large',    (fs + 2) + 'px');
            root.style.setProperty('--content-font-size',  (fs + 1) + 'px');
            root.classList.toggle('no-animations', !prefs.animations_enabled);
        })
        .catch(() => {
            window.amikoUiPrefs = {
                explorer_max_file_mb: 10,
                winbox_buttons: ['min','max','full','close'],
                ui_font_size: 13,
                clock_format: '24h',
                animations_enabled: true,
                explorer_show_hidden: false,
                explorer_confirm_delete: true
            };
        });

});
