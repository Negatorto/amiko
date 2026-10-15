/*
 * AMIKO — Notes Module
 * Multi-file notes with sharing, manual save, and desktop post-it icons.
 */

class NotesModule {
    constructor(container) {
        this.container = container;

        // DOM references
        this.hostLabel    = container.querySelector('.mod-notes-host');
        this.statusBar    = container.querySelector('.mod-notes-status');
        this.sidebar      = container.querySelector('.mod-notes-list');
        this.newBtn       = container.querySelector('.mod-notes-new-btn');
        this.editorEmpty  = container.querySelector('.mod-notes-editor-empty');
        this.editorActive = container.querySelector('.mod-notes-editor-active');
        this.titleInput   = container.querySelector('.mod-notes-title-input');
        this.textarea     = container.querySelector('.mod-notes-textarea');
        this.scopeSelect  = container.querySelector('.mod-notes-scope-select');
        this.groupSelect  = container.querySelector('.mod-notes-group-select');
        this.membersRow   = container.querySelector('.mod-notes-members-row');
        this.membersList  = container.querySelector('.mod-notes-members-list');
        this.saveBtn      = container.querySelector('.mod-notes-save-btn');
        this.deleteBtn    = container.querySelector('.mod-notes-delete-btn');

        // State
        this.currentHostname = null;
        this.currentGroupId  = null;
        this.currentServerId = null;
        this.notes           = [];
        this.activeNoteId    = null;
        this.allServers      = [];
        this.allGroups       = [];

        // Event handlers (for cleanup)
        this._onConnect    = this.onGlobalConnect.bind(this);
        this._onDisconnect = this.onGlobalDisconnect.bind(this);

        window.addEventListener('amikoGlobalConnect', this._onConnect);
        window.addEventListener('amikoGlobalDisconnect', this._onDisconnect);

        // Button handlers
        if (this.newBtn)    this.newBtn.addEventListener('click', () => this.createNote());
        if (this.saveBtn)   this.saveBtn.addEventListener('click', () => this.saveNote());
        if (this.deleteBtn) this.deleteBtn.addEventListener('click', () => this.deleteNote());

        // Scope select → toggle members list
        if (this.scopeSelect) {
            this.scopeSelect.addEventListener('change', () => {
                this._toggleMembersRow();
            });
        }

        // Check if already connected
        if (window.globalConnection && window.globalConnection.connected) {
            this.onGlobalConnect();
        }
    }

    // ──────────────────────────────────────
    //  Connection lifecycle
    // ──────────────────────────────────────

    async onGlobalConnect() {
        const gc = window.globalConnection;
        this.currentHostname = gc.hostname;
        this.currentServerId = gc.serverId || '';
        this.currentGroupId  = gc.groupId || '';

        if (this.hostLabel) this.hostLabel.textContent = this.currentHostname;

        // Load groups & servers for sharing UI
        await this._loadGroupsAndServers();

        // Load notes for current context
        await this.loadNotes();
    }

    onGlobalDisconnect() {
        this.currentHostname = null;
        this.currentGroupId  = null;
        this.currentServerId = null;
        this.activeNoteId    = null;
        this.notes           = [];

        if (this.hostLabel) this.hostLabel.textContent = 'Not connected';
        this._renderSidebar();
        this._showEditor(false);
        this.setStatus('idle');

        // Clear desktop icons
        this._clearDesktopIcons();
    }

    // ──────────────────────────────────────
    //  Data loading
    // ──────────────────────────────────────

    async _loadGroupsAndServers() {
        try {
            const [groups, servers] = await Promise.all([
                fetch('/api/groups').then(r => r.json()),
                fetch('/api/servers').then(r => r.json())
            ]);
            this.allGroups  = groups;
            this.allServers = servers;
            this._populateGroupSelect();
            this._populateMembersList();
        } catch (e) {
            console.error('[Notes] Error loading groups/servers:', e);
        }
    }

    async loadNotes() {
        if (!this.currentHostname) return;
        this.setStatus('loading');

        try {
            const params = new URLSearchParams({
                hostname: this.currentHostname,
                group_id: this.currentGroupId || '',
                server_id: this.currentServerId || ''
            });
            const res = await fetch(`/notes/list?${params}`);
            this.notes = await res.json();
            this._renderSidebar();
            this._renderDesktopIcons();

            // If active note is no longer in list, deselect
            if (this.activeNoteId && !this.notes.find(n => n.id === this.activeNoteId)) {
                this.activeNoteId = null;
                this._showEditor(false);
            }

            this.setStatus('idle');
        } catch (e) {
            console.error('[Notes] Error loading notes:', e);
            this.setStatus('error');
        }
    }

    // ──────────────────────────────────────
    //  CRUD operations
    // ──────────────────────────────────────

    async createNote() {
        if (!this.currentHostname) return;
        this.setStatus('saving');

        try {
            const res = await fetch('/notes/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    hostname: this.currentHostname,
                    title: 'Untitled',
                    content: '',
                    scope: 'private',
                    members: [],
                    group_scope: 'local'
                })
            });
            const note = await res.json();
            this.activeNoteId = note.id;
            await this.loadNotes();
            await this._openNote(note.id);
            this.setStatus('saved');
        } catch (e) {
            console.error('[Notes] Error creating note:', e);
            this.setStatus('error');
        }
    }

    async saveNote() {
        if (!this.activeNoteId) return;
        this.setStatus('saving');

        const members = this._getSelectedMembers();

        try {
            await fetch(`/notes/${this.activeNoteId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title: this.titleInput?.value || 'Untitled',
                    content: this.textarea?.value || '',
                    scope: this.scopeSelect?.value || 'private',
                    members: members,
                    group_scope: this.groupSelect?.value || 'local'
                })
            });

            // Refresh sidebar & desktop icons
            await this.loadNotes();

            // Visual feedback
            if (this.saveBtn) {
                const orig = this.saveBtn.textContent;
                this.saveBtn.textContent = '✅ Saved!';
                this.saveBtn.disabled = true;
                setTimeout(() => { this.saveBtn.textContent = orig; this.saveBtn.disabled = false; }, 1500);
            }
            this.setStatus('saved');
        } catch (e) {
            console.error('[Notes] Error saving note:', e);
            this.setStatus('error');
        }
    }

    async deleteNote() {
        if (!this.activeNoteId) return;

        const doDelete = async () => {
            this.setStatus('saving');
            try {
                await fetch(`/notes/${this.activeNoteId}`, { method: 'DELETE' });
                this.activeNoteId = null;
                this._showEditor(false);
                await this.loadNotes();
                this.setStatus('idle');
            } catch (e) {
                console.error('[Notes] Error deleting note:', e);
                this.setStatus('error');
            }
        };

        // Check user pref: skip confirm if disabled
        const skipConfirm = window.amikoUiPrefs?.explorer_confirm_delete === false;
        if (skipConfirm) {
            doDelete();
        } else if (window.AmikoConfirm) {
            window.AmikoConfirm('Delete Note', 'Are you sure? This cannot be undone.', doDelete);
        } else {
            doDelete();
        }
    }

    async _openNote(noteId) {
        this.activeNoteId = noteId;
        this.setStatus('loading');

        try {
            const res = await fetch(`/notes/${noteId}`);
            const note = await res.json();

            if (note.error) {
                this.setStatus('error');
                return;
            }

            // Populate editor
            if (this.titleInput) this.titleInput.value = note.title || '';
            if (this.textarea)   this.textarea.value   = note.content || '';

            // Sharing state
            if (this.scopeSelect) {
                this.scopeSelect.value = note.sharing?.scope || 'private';
                this._toggleMembersRow();
            }
            if (this.groupSelect) {
                this.groupSelect.value = note.group_scope || 'local';
            }

            // Members checkboxes
            const memberIds = new Set(note.sharing?.members || []);
            this.membersList?.querySelectorAll('input[type="checkbox"]').forEach(chk => {
                chk.checked = memberIds.has(chk.value);
            });

            this._showEditor(true);
            this._highlightSidebarItem(noteId);
            this.setStatus('idle');
        } catch (e) {
            console.error('[Notes] Error opening note:', e);
            this.setStatus('error');
        }
    }

    // ──────────────────────────────────────
    //  Sidebar rendering
    // ──────────────────────────────────────

    _renderSidebar() {
        if (!this.sidebar) return;
        this.sidebar.innerHTML = '';

        if (!this.notes.length) {
            const empty = document.createElement('div');
            empty.style.cssText = 'padding: 20px 12px; color: var(--text-dim); font-size: 0.75rem; text-align: center;';
            empty.textContent = this.currentHostname ? 'No notes yet' : 'Connect to a server';
            this.sidebar.appendChild(empty);
            return;
        }

        for (const note of this.notes) {
            const item = document.createElement('div');
            item.className = 'mod-notes-item' + (note.id === this.activeNoteId ? ' active' : '');
            item.dataset.noteId = note.id;

            const scope = note.sharing?.scope || 'private';
            const badge = scope === 'private' ? '🔒' : scope === 'all' ? '🌐' : '👥';

            const dateStr = this._formatDate(note.updated_at || note.created_at);

            item.innerHTML = `
                <div class="mod-notes-item-title">${this._esc(note.title)}</div>
                <div class="mod-notes-item-meta">
                    <span>${dateStr}</span>
                    <span class="mod-notes-item-badge">${badge}</span>
                </div>
                <div class="mod-notes-item-preview">${this._esc(note.preview || '')}</div>
            `;

            item.addEventListener('click', () => this._openNote(note.id));
            this.sidebar.appendChild(item);
        }
    }

    _highlightSidebarItem(noteId) {
        if (!this.sidebar) return;
        this.sidebar.querySelectorAll('.mod-notes-item').forEach(el => {
            el.classList.toggle('active', el.dataset.noteId === noteId);
        });
    }

    // ──────────────────────────────────────
    //  Editor show/hide
    // ──────────────────────────────────────

    _showEditor(show) {
        if (this.editorEmpty)  this.editorEmpty.style.display  = show ? 'none' : 'flex';
        if (this.editorActive) this.editorActive.style.display = show ? 'flex' : 'none';
    }

    // ──────────────────────────────────────
    //  Sharing UI helpers
    // ──────────────────────────────────────

    _populateGroupSelect() {
        if (!this.groupSelect) return;
        // Keep "Local" as first option
        this.groupSelect.innerHTML = '<option value="local">📌 Local (this machine only)</option>';
        for (const g of this.allGroups) {
            const opt = document.createElement('option');
            opt.value = g.id;
            opt.textContent = `${g.icon || '📁'} ${g.name}`;
            this.groupSelect.appendChild(opt);
        }
    }

    _populateMembersList() {
        if (!this.membersList) return;
        this.membersList.innerHTML = '';
        for (const s of this.allServers) {
            const label = document.createElement('label');
            label.className = 'mod-notes-member-chk';
            label.innerHTML = `<input type="checkbox" value="${s.id}"> ${this._esc(s.name)}`;
            this.membersList.appendChild(label);
        }
    }

    _toggleMembersRow() {
        if (!this.membersRow || !this.scopeSelect) return;
        this.membersRow.style.display = this.scopeSelect.value === 'members' ? 'block' : 'none';
    }

    _getSelectedMembers() {
        if (!this.membersList) return [];
        const checked = [];
        this.membersList.querySelectorAll('input[type="checkbox"]:checked').forEach(chk => {
            checked.push(chk.value);
        });
        return checked;
    }

    // ──────────────────────────────────────
    //  Desktop Post-it Icons
    // ──────────────────────────────────────

    _renderDesktopIcons() {
        this._clearDesktopIcons();
        const desktop = document.getElementById('amikoDesktop');
        if (!desktop || !this.notes.length) return;

        const startX = 20;
        const startY = 20;
        const colGap = 96;
        const rowGap = 96;
        const maxCols = Math.max(1, Math.floor((desktop.clientWidth - 40) / colGap));

        this.notes.forEach((note, i) => {
            const col = i % maxCols;
            const row = Math.floor(i / maxCols);
            const x = startX + col * colGap;
            const y = startY + row * rowGap;

            const scope = note.sharing?.scope || 'private';
            const icon = document.createElement('div');
            icon.className = 'desktop-note-icon';
            icon.dataset.noteDesktopId = note.id;
            icon.style.left = x + 'px';
            icon.style.top  = y + 'px';

            icon.innerHTML = `
                <div class="desktop-note-postit" data-scope="${scope}">📝</div>
                <span class="desktop-note-label">${this._esc(note.title)}</span>
            `;

            icon.addEventListener('click', () => {
                // Open Notes module and select this note
                this._openNote(note.id);
                // Make sure the WinBox is visible
                const dockBtn = document.querySelector('.dock-item[data-type="notes"]');
                if (dockBtn && !dockBtn.classList.contains('active')) {
                    dockBtn.click();
                }
            });

            desktop.appendChild(icon);
        });
    }

    _clearDesktopIcons() {
        const desktop = document.getElementById('amikoDesktop');
        if (!desktop) return;
        desktop.querySelectorAll('.desktop-note-icon').forEach(el => el.remove());
    }

    // ──────────────────────────────────────
    //  Status & helpers
    // ──────────────────────────────────────

    setStatus(state) {
        if (!this.statusBar) return;
        const states = {
            idle:    '⏳ Ready',
            loading: '⏳ Loading…',
            saving:  '💾 Saving…',
            saved:   '✅ Saved',
            error:   '❌ Error'
        };
        this.statusBar.textContent = states[state] || '';
    }

    _formatDate(iso) {
        if (!iso) return '';
        try {
            const d = new Date(iso);
            const use12h = window.amikoUiPrefs?.clock_format === '12h';
            return d.toLocaleDateString('it-IT', { day: '2-digit', month: 'short' })
                + ' ' + d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit', hour12: use12h });
        } catch {
            return '';
        }
    }

    _esc(str) {
        const d = document.createElement('div');
        d.textContent = str || '';
        return d.innerHTML;
    }

    // ──────────────────────────────────────
    //  Cleanup
    // ──────────────────────────────────────

    destroy() {
        window.removeEventListener('amikoGlobalConnect', this._onConnect);
        window.removeEventListener('amikoGlobalDisconnect', this._onDisconnect);
        this._clearDesktopIcons();
    }
}

// Register Module
AmikoModules.register('notes', NotesModule);
