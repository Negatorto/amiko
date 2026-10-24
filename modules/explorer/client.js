/*
 * AMIKO - Explorer Module
 * Point-and-click file browser for remote servers
 * With popup code editor for text files
 */

class ExplorerModule {
    constructor(container) {
        this.container = container;
        this.currentPath = '/';

        // DOM references
        this.breadcrumb = container.querySelector('.mod-explorer-breadcrumb');
        this.pathInput = container.querySelector('.mod-explorer-path-input');
        this.btnGo = container.querySelector('.mod-explorer-btn-go');
        this.btnUp = container.querySelector('.mod-explorer-btn-up');
        this.btnRefresh = container.querySelector('.mod-explorer-btn-refresh');
        this.tbody = container.querySelector('.mod-explorer-tbody');
        this.loadingOverlay = container.querySelector('.mod-explorer-loading');
        this.crumbRoot = container.querySelector('.mod-explorer-crumb-root');

        // In-memory directory cache for instant navigation (path -> entries JSON string)
        this._dirCache = new Map();

        // Editor popup state
        this.editorPopup = null;
        this.editorTextarea = null;
        this.editorLineNumbers = null;
        this.currentFilePath = null;
        this.originalContent = '';
        this.isModified = false;

        // Context menu
        this.contextMenu = null;
        this._hideContextMenuBound = this._hideContextMenu.bind(this);

        // Navigation abort controller (cancel in-flight requests on fast navigation)
        this._navAbortController = null;

        // Debounced line-numbers update (avoids full DOM reflow on every keystroke)
        this._debouncedLineNumbers = this._debounce(this.updateLineNumbers.bind(this), 120);

        // Bind events
        if (this.btnGo) {
            this.btnGo.addEventListener('click', () => {
                const path = this.pathInput.value.trim() || '/';
                this.navigateTo(path);
            });
        }

        if (this.btnUp) {
            this.btnUp.addEventListener('click', () => {
                if (this.currentPath !== '/') {
                    const parentPath = this.currentPath.replace(/\/[^/]+\/?$/, '') || '/';
                    this.navigateTo(parentPath);
                }
            });
        }

        if (this.btnRefresh) {
            this.btnRefresh.addEventListener('click', () => {
                this.navigateTo(this.currentPath, true);
            });
        }

        if (this.pathInput) {
            this.pathInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    const path = this.pathInput.value.trim() || '/';
                    this.navigateTo(path);
                }
            });
        }

        if (this.crumbRoot) {
            this.crumbRoot.addEventListener('click', () => this.navigateTo('/'));
        }

        // Single delegated event listeners on tbody (eliminates 3*N per-row listeners)
        if (this.tbody) {
            this.tbody.addEventListener('click', (e) => {
                const row = e.target.closest('tr');
                if (!row) return;
                if (row.dataset.parentNav === 'true') {
                    const parentPath = this.currentPath.replace(/\/[^/]+\/?$/, '') || '/';
                    this.navigateTo(parentPath);
                    return;
                }
                if (row.dataset.entryName) {
                    const prev = this.tbody.querySelector('tr.is-selected');
                    if (prev && prev !== row) prev.classList.remove('is-selected');
                    row.classList.add('is-selected');
                }
            });

            this.tbody.addEventListener('dblclick', (e) => {
                const row = e.target.closest('tr');
                if (!row) return;
                if (row.dataset.parentNav === 'true') {
                    const parentPath = this.currentPath.replace(/\/[^/]+\/?$/, '') || '/';
                    this.navigateTo(parentPath);
                    return;
                }
                const entryName = row.dataset.entryName;
                if (!entryName) return;
                const entryIsDir = row.dataset.entryIsDir === 'true';
                this._openEntry(entryName, entryIsDir);
            });
        }

        // Grid span override removed in favor of fluid CSS Grid
        this.gridCell = this.container.closest('.grid-cell');

        // Global connection events
        this.globalConnectHandler = this.onGlobalConnect.bind(this);
        this.globalDisconnectHandler = this.onGlobalDisconnect.bind(this);
        window.addEventListener('amikoGlobalConnect', this.globalConnectHandler);
        window.addEventListener('amikoGlobalDisconnect', this.globalDisconnectHandler);

        // Context menu — right-click on listing container (bubbles from rows)
        const listingContainer = container.querySelector('.mod-explorer-listing-container');
        if (listingContainer) {
            listingContainer.addEventListener('contextmenu', (e) => this._onTableContextMenu(e));
        }
        // Dismiss context menu on any click outside
        document.addEventListener('click', this._hideContextMenuBound);
        document.addEventListener('contextmenu', this._hideContextMenuBound, true); // capture phase so it fires before new menus

        // If already connected, navigate to root immediately
        if (window.globalConnection && window.globalConnection.connected) {
            this.navigateTo('/');
        }
    }

    onGlobalConnect() {
        this._dirCache.clear();
        this.navigateTo('/', true);
    }

    onGlobalDisconnect() {
        this._dirCache.clear();
        this.currentPath = '/';
        this.pathInput.value = '/';
        this.renderBreadcrumb('/');
        this.tbody.innerHTML = `
            <tr>
                <td colspan="2" style="padding: 20px 12px; text-align: center; opacity: 0.4;">
                    // Connect to a server to browse files...
                </td>
            </tr>`;
        this.closeEditor();
    }

    showLoading(message) {
        if (this.loadingOverlay) {
            const text = this.loadingOverlay.querySelector('.mod-explorer-loading-text');
            if (text) text.textContent = message || '⏳ Loading...';
            this.loadingOverlay.style.display = 'flex';
        }
    }

    hideLoading() {
        if (this.loadingOverlay) {
            this.loadingOverlay.style.display = 'none';
        }
    }

    buildPayload(extraParams) {
        if (!window.globalConnection || !window.globalConnection.connected) {
            return null;
        }
        const payload = {
            session_id: window.globalConnection.sessionId,
            server_id: window.globalConnection.serverId,
            hostname: window.globalConnection.hostname,
            port: window.globalConnection.port,
            username: window.globalConnection.username
        };

        if (extraParams) {
            Object.assign(payload, extraParams);
        }
        return payload;
    }

    // ──────────────────────────────────────
    //  Directory Navigation
    // ──────────────────────────────────────

    async navigateTo(path, forceRefresh = false) {
        if (!window.globalConnection || !window.globalConnection.connected) {
            if (window.AmikoAlert) window.AmikoAlert('Error', 'You must connect first using the global connection form!');
            return;
        }

        path = path.replace(/\/+/g, '/') || '/';
        if (!path.startsWith('/')) path = '/' + path;

        // Abort any in-flight navigation request
        if (this._navAbortController) {
            this._navAbortController.abort();
        }
        this._navAbortController = new AbortController();
        const signal = this._navAbortController.signal;

        // Instant Stale-While-Revalidate render if directory is cached
        const cachedJson = !forceRefresh ? this._dirCache.get(path) : null;
        if (cachedJson) {
            this.currentPath = path;
            this.pathInput.value = path;
            this.renderBreadcrumb(path);
            this.renderEntries(JSON.parse(cachedJson));
        } else {
            this.showLoading('⏳');
        }

        const payload = this.buildPayload({ path: path });
        if (!payload) return;

        try {
            const response = await fetch('/explorer/list-dir', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
                signal
            });
            const data = await response.json();

            if (data.status === 'ok') {
                const freshJson = JSON.stringify(data.entries);
                this._dirCache.set(data.path, freshJson);
                // Cap cache at 50 directories
                if (this._dirCache.size > 50) {
                    const oldestKey = this._dirCache.keys().next().value;
                    this._dirCache.delete(oldestKey);
                }
                if (!cachedJson || cachedJson !== freshJson || this.currentPath !== data.path) {
                    this.currentPath = data.path;
                    this.pathInput.value = data.path;
                    this.renderBreadcrumb(data.path);
                    this.renderEntries(data.entries);
                }
            } else if (!cachedJson) {
                this.renderError(data.message || 'Unknown error.');
            }
        } catch (err) {
            if (err.name === 'AbortError') return; // Silently ignore cancelled requests
            if (!cachedJson) this.renderError('Network error: ' + err.message);
        } finally {
            this.hideLoading();
        }
    }

    // ──────────────────────────────────────
    //  File Editor Popup
    // ──────────────────────────────────────

    async openFile(path) {
        if (!window.globalConnection || !window.globalConnection.connected) return;

        this.showLoading('⏳ Reading file...');

        const payload = this.buildPayload({ path: path });
        if (!payload) return;

        try {
            const response = await fetch('/explorer/read-file', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await response.json();

            if (data.status === 'ok') {
                this.currentFilePath = data.path;
                this.originalContent = data.content;
                this.isModified = false;
                this.showEditor(data.filename, data.content, data.size_display, data.path);
            } else {
                if (window.AmikoAlert) window.AmikoAlert('Error', data.message || 'Cannot read file.');
            }
        } catch (err) {
            if (window.AmikoAlert) window.AmikoAlert('Error', 'Network error: ' + err.message);
        } finally {
            this.hideLoading();
        }
    }

    showEditor(filename, content, sizeDisplay, filePath) {
        // Remove any existing popup
        if (this.editorPopup) {
            this.editorPopup.close(true);
        }

        const popup = document.createElement('div');
        popup.style.cssText = 'display: flex; flex-direction: column; width: 100%; height: 100%; background: var(--bg-primary);';

        const toolbar = document.createElement('div');
        toolbar.style.cssText = `
            display: flex; align-items: center; justify-content: space-between;
            padding: 10px 16px; background: var(--bg-secondary);
            border-bottom: 1px solid var(--border-medium, rgba(255,255,255,0.1));
            font-family: monospace; font-size: 0.9em; flex-shrink: 0;
            color: var(--text-primary);
        `;

        // Left side: file info
        const fileInfo = document.createElement('div');
        fileInfo.style.cssText = 'display: flex; align-items: center; gap: 12px; overflow: hidden; flex: 1;';
        fileInfo.innerHTML = `
            <span style="opacity: 0.4; white-space: nowrap;">${sizeDisplay}</span>
            <span class="amiko-editor-modified-badge" style="display: none; color: #ff6b6b; font-size: 0.8em; white-space: nowrap;">● MODIFIED</span>
        `;
        toolbar.appendChild(fileInfo);

        // Right side: buttons (or Read-Only badge for Viewer role)
        const isReadOnly = window.amikoCurrentUser && window.amikoCurrentUser.role === 'viewer';
        const buttons = document.createElement('div');
        buttons.style.cssText = 'display: flex; gap: 8px; flex-shrink: 0; align-items: center;';

        if (isReadOnly) {
            const roBadge = document.createElement('span');
            roBadge.textContent = '🔒 READ-ONLY';
            roBadge.style.cssText = `
                background: rgba(240, 192, 64, 0.15); border: 1px solid rgba(240, 192, 64, 0.4);
                color: var(--accent-warning, #f0c040); padding: 4px 12px; border-radius: 4px;
                font-family: monospace; font-size: 0.8em;
            `;
            buttons.appendChild(roBadge);
        } else {
            const btnSave = document.createElement('button');
            btnSave.innerHTML = '<svg class="md-icon"><use href="#md-save"></use></svg> SAVE';
            btnSave.style.cssText = `
                background: rgba(0, 180, 80, 0.15); border: 1px solid rgba(0, 180, 80, 0.4);
                color: #4cff88; cursor: pointer; padding: 5px 16px; border-radius: 4px;
                font-family: monospace; font-size: 0.85em; transition: all 0.2s;
                display: inline-flex; align-items: center; gap: 6px;
            `;
            btnSave.addEventListener('mouseenter', () => { btnSave.style.background = 'rgba(0, 180, 80, 0.3)'; });
            btnSave.addEventListener('mouseleave', () => { btnSave.style.background = 'rgba(0, 180, 80, 0.15)'; });
            btnSave.addEventListener('click', () => this.saveFile());
            buttons.appendChild(btnSave);
        }

        toolbar.appendChild(buttons);
        popup.appendChild(toolbar);

        // ── Editor Area ──
        const editorArea = document.createElement('div');
        editorArea.style.cssText = 'flex: 1; display: flex; overflow: hidden;';

        const lineNumbers = document.createElement('div');
        lineNumbers.className = 'amiko-editor-line-numbers';
        lineNumbers.style.cssText = `
            width: 55px; flex-shrink: 0; overflow: hidden; background: var(--bg-secondary);
            border-right: 1px solid var(--border-medium, rgba(255, 255, 255, 0.08)); padding: 12px 0; font-family: monospace;
            font-size: 0.82em; line-height: 1.55; color: var(--text-dim); text-align: right; user-select: none;
        `;
        this.editorLineNumbers = lineNumbers;

        const textarea = document.createElement('textarea');
        textarea.className = 'amiko-editor-textarea';
        textarea.value = content;
        textarea.spellcheck = false;
        textarea.readOnly = isReadOnly;
        textarea.style.cssText = `
            flex: 1; resize: none; border: none; outline: none; background: transparent; color: var(--text-primary);
            padding: 12px 16px; margin: 0; font-family: 'Fira Code', 'Cascadia Code', 'JetBrains Mono', 'Consolas', monospace;
            font-size: 0.82em; line-height: 1.55; tab-size: 4; white-space: pre; overflow: auto;
        `;
        this.editorTextarea = textarea;

        textarea.addEventListener('scroll', () => {
            lineNumbers.scrollTop = textarea.scrollTop;
        });

        textarea.addEventListener('input', () => {
            this.isModified = (textarea.value !== this.originalContent);
            const badge = popup.querySelector('.amiko-editor-modified-badge');
            if (badge) badge.style.display = this.isModified ? 'inline' : 'none';
            // Debounced: avoids full DOM reflow on every single keystroke
            this._debouncedLineNumbers(textarea.value);
        });

        textarea.addEventListener('keydown', (e) => {
            if (e.key === 'Tab') {
                e.preventDefault();
                const start = textarea.selectionStart;
                const end = textarea.selectionEnd;
                textarea.value = textarea.value.substring(0, start) + '    ' + textarea.value.substring(end);
                textarea.selectionStart = textarea.selectionEnd = start + 4;
                textarea.dispatchEvent(new Event('input'));
            }
            if ((e.ctrlKey || e.metaKey) && e.key === 's') {
                e.preventDefault();
                this.saveFile();
            }
            if (e.key === 'Escape') {
                e.preventDefault();
                this.closeEditor();
            }
        });

        editorArea.appendChild(lineNumbers);
        editorArea.appendChild(textarea);
        popup.appendChild(editorArea);

        // ── Status Bar ──
        const statusBar = document.createElement('div');
        statusBar.className = 'amiko-editor-statusbar';
        statusBar.style.cssText = `
            display: flex; justify-content: space-between; align-items: center; padding: 4px 16px;
            background: var(--bg-secondary); border-top: 1px solid var(--border-medium, rgba(255,255,255,0.08));
            font-family: monospace; font-size: 0.75em; color: var(--text-dim); flex-shrink: 0;
        `;
        const lines = content.split('\n');
        statusBar.innerHTML = `
            <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;">${this.escapeHtml(filePath)}</span>
            <span style="white-space: nowrap; margin-left: 16px;">${lines.length} lines  |  UTF-8  |  Ctrl+S: Save  |  Esc: Close</span>
        `;
        popup.appendChild(statusBar);

        textarea.addEventListener('click', () => this.updateCursorPos(textarea, statusBar, filePath));
        textarea.addEventListener('keyup', () => this.updateCursorPos(textarea, statusBar, filePath));

        // Use WinBox
        this.editorPopup = new WinBox({
            title: `📝 ${filename}`,
            class: ['wb-amiko', 'wb-amiko-explorer'],
            mount: popup,
            width: 650,
            height: 450,
            x: "center",
            y: "center",
            top: 70, // Prevents window from maximizing or dragging under the AMIKO header
            index: 9999, // Ensures it sits above all other UI elements like the header
            onclose: (force) => {
                if (!force && this.isModified) {
                    if (window.AmikoConfirm) {
                        window.AmikoConfirm('Unsaved Changes', 'You have unsaved changes. Close anyway?', () => {
                            this.isModified = false;
                            this.editorPopup.close(true);
                        });
                        return true;
                    } else if (!confirm('You have unsaved changes. Close anyway?')) {
                        return true; // Keeps window open
                    }
                }
                this.editorPopup = null;
                this.editorTextarea = null;
                this.editorLineNumbers = null;
                this.currentFilePath = null;
                this.originalContent = '';
                this.isModified = false;
            }
        });

        // Native WinBox controls handle the close button natively.
        this.updateLineNumbers(content);
        textarea.focus();
        textarea.setSelectionRange(0, 0);
    }

    updateLineNumbers(content) {
        if (!this.editorLineNumbers) return;
        const lines = content.split('\n');
        const nums = [];
        for (let i = 1; i <= lines.length; i++) {
            nums.push(`<div style="padding: 0 8px;">${i}</div>`);
        }
        this.editorLineNumbers.innerHTML = nums.join('');
    }

    updateCursorPos(textarea, statusBar, filePath) {
        const val = textarea.value;
        const pos = textarea.selectionStart;
        const before = val.substring(0, pos);
        const line = before.split('\n').length;
        const col = pos - before.lastIndexOf('\n');
        const totalLines = val.split('\n').length;

        statusBar.innerHTML = `
            <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;">${this.escapeHtml(filePath)}</span>
            <span style="white-space: nowrap; margin-left: 16px;">Ln ${line}, Col ${col}  |  ${totalLines} lines  |  UTF-8  |  Ctrl+S: Save  |  Esc: Close</span>
        `;
    }

    async saveFile() {
        if (!this.currentFilePath || !this.editorTextarea) return;
        if (!window.globalConnection || !window.globalConnection.connected) return;

        const content = this.editorTextarea.value;

        const payload = this.buildPayload({ path: this.currentFilePath, content: content });
        if (!payload) return;

        try {
            const response = await fetch('/explorer/save-file', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const data = await response.json();

            if (data.status === 'ok') {
                this.originalContent = content;
                this.isModified = false;
                // Badge might be inside the WinBox mount point now. However, easier to just hide it via query selector inside editor popup.
                if(this.editorPopup && this.editorPopup.body) {
                    const badge = this.editorPopup.body.querySelector('.amiko-editor-modified-badge');
                    if (badge) badge.style.display = 'none';
                }

                // Flash save confirmation
                this.flashStatusMsg('✅ File saved successfully!');
            } else {
                if (window.AmikoAlert) window.AmikoAlert('Error', 'Save failed: ' + (data.message || 'Unknown error'));
            }
        } catch (err) {
            if (window.AmikoAlert) window.AmikoAlert('Error', 'Save error: ' + err.message);
        }
    }

    flashStatusMsg(msg) {
        if(!this.editorPopup || !this.editorPopup.body) return;
        const statusBar = this.editorPopup.body.querySelector('.amiko-editor-statusbar');
        if (!statusBar) return;
        const flash = document.createElement('span');
        flash.textContent = msg;
        flash.style.cssText = 'color: #4cff88; margin-left: 12px; transition: opacity 0.5s;';
        statusBar.appendChild(flash);
        setTimeout(() => { flash.style.opacity = '0'; }, 1500);
        setTimeout(() => flash.remove(), 2200);
    }

    async openImage(path, filename) {
        if (!window.globalConnection || !window.globalConnection.connected) return;

        this.showLoading('⏳ Loading image...');
        const payload = this.buildPayload({ path: path });
        if (!payload) return;

        try {
            const response = await fetch('/explorer/read-image', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await response.json();

            if (data.status === 'ok') {
                const imgExt = filename.split('.').pop().toLowerCase();
                const mimeType = imgExt === 'svg' ? 'image/svg+xml' : `image/${imgExt}`;
                const imgContent = `<img src="data:${mimeType};base64,${data.image_data}" style="max-width: 100%; max-height: 100%; object-fit: contain; background: var(--bg-primary); display: block; margin: auto;">`;
                
                const imgBox = new WinBox({
                    title: `🖼️ ${filename}`,
                    class: ['wb-amiko', 'wb-amiko-explorer'],
                    html: imgContent,
                    width: 650,
                    height: 450,
                    x: "center",
                    y: "center",
                    top: 70,
                    index: 9999,
                    background: 'var(--bg-primary)'
                });
            } else {
                if (window.AmikoAlert) window.AmikoAlert('Error', data.message || 'Cannot open image.');
            }
        } catch (err) {
            if (window.AmikoAlert) window.AmikoAlert('Error', 'Network error: ' + err.message);
        } finally {
            this.hideLoading();
        }
    }

    closeEditor() {
        if (this.editorPopup) {
            this.editorPopup.close(); // let onclose handle it cleanly
        }
    }

    // ──────────────────────────────────────
    //  Context Menu
    // ──────────────────────────────────────

    _onTableContextMenu(e) {
        e.preventDefault();
        e.stopPropagation();

        if (!window.globalConnection || !window.globalConnection.connected) return;

        // Find if we right-clicked on a data row (has data-entry-name)
        const row = e.target.closest('tr[data-entry-name]');
        const entryName = row ? row.dataset.entryName : null;
        const entryIsDir = row ? row.dataset.entryIsDir === 'true' : null;

        const isReadOnly = window.amikoCurrentUser && window.amikoCurrentUser.role === 'viewer';
        const items = [];

        if (entryName) {
            items.push({
                icon: entryIsDir
                    ? '<svg class="md-icon"><use href="#md-folder"></use></svg>'
                    : (isReadOnly ? '<svg class="md-icon"><use href="#md-file"></use></svg>' : '<svg class="md-icon"><use href="#md-edit"></use></svg>'),
                label: entryIsDir ? 'Open' : (isReadOnly ? 'View (Read-Only)' : 'Open / Edit'),
                action: () => this._openEntry(entryName, entryIsDir)
            });
            if (!isReadOnly) items.push({ separator: true });
        }

        if (!isReadOnly) {
            items.push(
                {
                    icon: '<svg class="md-icon"><use href="#md-file"></use></svg>',
                    label: 'New File',
                    action: () => this._createNewFile()
                },
                {
                    icon: '<svg class="md-icon"><use href="#md-folder"></use></svg>',
                    label: 'New Folder',
                    action: () => this._createNewFolder()
                }
            );

            if (entryName) {
                items.push({ separator: true });
                items.push({
                    icon: '<svg class="md-icon"><use href="#md-delete"></use></svg>',
                    label: `Delete "${entryName}"`,
                    danger: true,
                    action: () => this._deleteEntry(entryName, entryIsDir)
                });
            }
        }

        if (items.length === 0) return;
        this._showContextMenu(e.clientX, e.clientY, items);
    }

    _showContextMenu(x, y, items) {
        // Remove any existing menu
        this._hideContextMenu();

        const menu = document.createElement('div');
        menu.style.cssText = `
            position: fixed;
            z-index: 99999;
            left: ${x}px;
            top: ${y}px;
            background: var(--bg-secondary, #1a1a2e);
            border: 1px solid var(--border-color, rgba(255,255,255,0.15));
            border-radius: 6px;
            padding: 4px 0;
            min-width: 180px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.5);
            font-family: monospace;
            font-size: 0.85em;
            animation: ctxMenuFadeIn 0.12s ease;
        `;

        // Inject animation keyframe once
        if (!document.getElementById('amiko-ctx-menu-style')) {
            const style = document.createElement('style');
            style.id = 'amiko-ctx-menu-style';
            style.textContent = `
                @keyframes ctxMenuFadeIn {
                    from { opacity: 0; transform: translateY(-4px) scale(0.97); }
                    to   { opacity: 1; transform: translateY(0)   scale(1);    }
                }
            `;
            document.head.appendChild(style);
        }

        items.forEach(item => {
            if (item.separator) {
                const sep = document.createElement('div');
                sep.style.cssText = 'border-top: 1px solid var(--border-color, rgba(255,255,255,0.1)); margin: 4px 0;';
                menu.appendChild(sep);
                return;
            }

            const btn = document.createElement('div');
            btn.style.cssText = `
                display: flex;
                align-items: center;
                gap: 10px;
                padding: 8px 16px;
                cursor: pointer;
                color: ${item.danger ? 'var(--accent-danger, #ff4444)' : 'var(--text-primary, #0f0)'};
                transition: background 0.1s;
                user-select: none;
            `;
            btn.innerHTML = `<span>${item.icon}</span><span>${this.escapeHtml(item.label)}</span>`;
            btn.addEventListener('mouseenter', () => {
                btn.style.background = item.danger
                    ? 'rgba(255, 68, 68, 0.12)'
                    : 'var(--glow-primary, rgba(255,255,255,0.07))';
            });
            btn.addEventListener('mouseleave', () => { btn.style.background = 'transparent'; });
            btn.addEventListener('click', (ev) => {
                ev.stopPropagation();
                this._hideContextMenu();
                item.action();
            });
            menu.appendChild(btn);
        });

        document.body.appendChild(menu);
        this.contextMenu = menu;

        // Clamp to viewport
        const rect = menu.getBoundingClientRect();
        if (rect.right > window.innerWidth)  menu.style.left = (x - rect.width)  + 'px';
        if (rect.bottom > window.innerHeight) menu.style.top  = (y - rect.height) + 'px';
    }

    _hideContextMenu() {
        if (this.contextMenu) {
            this.contextMenu.remove();
            this.contextMenu = null;
        }
    }

    _openEntry(name, isDir) {
        const fullPath = this.currentPath === '/'
            ? '/' + name
            : this.currentPath.replace(/\/$/, '') + '/' + name;

        if (isDir) {
            this.navigateTo(fullPath);
        } else {
            const ext = name.split('.').pop().toLowerCase();
            const imageExts = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'ico'];
            if (imageExts.includes(ext)) {
                this.openImage(fullPath, name);
            } else {
                this.openFile(fullPath);
            }
        }
    }

    async _createNewFile() {

        if (!window.AmikoPrompt) return;
        window.AmikoPrompt('New File', 'Enter file name:', 'newfile.txt', async (name) => {
            name = name ? name.trim() : '';
            if (!name) return;

            const fullPath = this.currentPath === '/'
                ? '/' + name
                : this.currentPath.replace(/\/$/, '') + '/' + name;

            const payload = this.buildPayload({ path: fullPath, content: '' });
            if (!payload) return;

            try {
                const res  = await fetch('/explorer/create-file', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify(payload)
                });
                const data = await res.json();

                if (data.status === 'ok') {
                    this._dirCache.delete(this.currentPath);
                    // Refresh listing, then open the new file in the editor
                    await this.navigateTo(this.currentPath, true);
                    this.openFile(fullPath);
                } else {
                    if (window.AmikoAlert) window.AmikoAlert('Error', data.message || 'Cannot create file.', 'error');
                }
            } catch (err) {
                if (window.AmikoAlert) window.AmikoAlert('Error', 'Network error: ' + err.message, 'error');
            }
        });
    }

    async _createNewFolder() {
        if (!window.AmikoPrompt) return;
        window.AmikoPrompt('New Folder', 'Enter folder name:', 'new_folder', async (name) => {
            name = name ? name.trim() : '';
            if (!name) return;

            const fullPath = this.currentPath === '/'
                ? '/' + name
                : this.currentPath.replace(/\/$/, '') + '/' + name;

            const payload = this.buildPayload({ path: fullPath });
            if (!payload) return;

            try {
                const res  = await fetch('/explorer/create-dir', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify(payload)
                });
                const data = await res.json();

                if (data.status === 'ok') {
                    this._dirCache.delete(this.currentPath);
                    await this.navigateTo(this.currentPath, true);
                } else {
                    if (window.AmikoAlert) window.AmikoAlert('Error', data.message || 'Cannot create folder.', 'error');
                }
            } catch (err) {
                if (window.AmikoAlert) window.AmikoAlert('Error', 'Network error: ' + err.message, 'error');
            }
        });
    }

    _deleteEntry(name, isDir) {
        const fullPath = this.currentPath === '/'
            ? '/' + name
            : this.currentPath.replace(/\/$/, '') + '/' + name;

        const label = isDir ? `folder "${name}" and all its contents` : `file "${name}"`;

        const doDelete = async () => {
            const payload = this.buildPayload({ path: fullPath, is_dir: isDir });
            if (!payload) return;
            try {
                const res  = await fetch('/explorer/delete-entry', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.status === 'ok') {
                    this._dirCache.delete(this.currentPath);
                    if (isDir) this._dirCache.delete(fullPath);
                    await this.navigateTo(this.currentPath, true);
                } else {
                    if (window.AmikoAlert) window.AmikoAlert('Error', data.message || 'Cannot delete.', 'error');
                }
            } catch (err) {
                if (window.AmikoAlert) window.AmikoAlert('Error', 'Network error: ' + err.message, 'error');
            }
        };

        // Check user pref: skip confirm dialog if disabled
        const skipConfirm = window.amikoUiPrefs?.explorer_confirm_delete === false;
        if (skipConfirm) {
            doDelete();
        } else {
            window.AmikoConfirm(
                'Delete',
                `Are you sure you want to delete ${label}? This cannot be undone.`,
                doDelete
            );
        }
    }

    // ──────────────────────────────────────
    //  File Listing Rendering (Batched DOM)
    // ──────────────────────────────────────

    renderEntries(entries) {
        const frag = document.createDocumentFragment();

        // Parent directory row (go up on single click or double click)
        if (this.currentPath !== '/') {
            const upRow = document.createElement('tr');
            upRow.className = 'mod-explorer-row';
            upRow.dataset.parentNav = 'true';
            upRow.innerHTML = `
                <td style="padding: 7px 12px; color: var(--text-primary);"><span style="display:inline-flex; align-items:center; gap:6px;"><svg class="md-icon"><use href="#md-folder"></use></svg> ..</span></td>
                <td style="padding: 7px 12px; text-align: right; color: var(--text-dim);">—</td>`;
            frag.appendChild(upRow);
        }

        if (entries.length === 0) {
            const emptyRow = document.createElement('tr');
            emptyRow.innerHTML = `
                <td colspan="2" style="padding: 20px 12px; text-align: center; opacity: 0.4;">
                    // Directory is empty
                </td>`;
            frag.appendChild(emptyRow);
            this.tbody.replaceChildren(frag);
            return;
        }

        for (let i = 0; i < entries.length; i++) {
            const entry = entries[i];
            const row = document.createElement('tr');
            row.className = 'mod-explorer-row';
            row.dataset.entryName  = entry.name;
            row.dataset.entryIsDir = entry.is_dir;

            const icon = entry.is_dir
                ? '<svg class="md-icon"><use href="#md-folder"></use></svg>'
                : '<svg class="md-icon" style="opacity:0.75;"><use href="#md-file"></use></svg>';
            row.innerHTML = `
                <td style="padding: 7px 12px; color: var(--text-primary);"><span style="display:inline-flex; align-items:center; gap:6px;">${icon} ${this.escapeHtml(entry.name)}</span></td>
                <td style="padding: 7px 12px; text-align: right; color: var(--text-dim);">${entry.size_display}</td>`;
            frag.appendChild(row);
        }

        this.tbody.replaceChildren(frag);
    }

    renderBreadcrumb(path) {
        const existingCrumbs = this.breadcrumb.querySelectorAll('.mod-explorer-crumb-part');
        existingCrumbs.forEach(el => el.remove());

        if (path === '/') return;

        const parts = path.split('/').filter(p => p);
        let accumulated = '';

        parts.forEach((part, index) => {
            accumulated += '/' + part;
            const partPath = accumulated;

            const sep = document.createElement('span');
            sep.textContent = ' › ';
            sep.style.opacity = '0.4';
            sep.className = 'mod-explorer-crumb-part';
            this.breadcrumb.appendChild(sep);

            const crumb = document.createElement('span');
            crumb.textContent = part;
            crumb.className = 'mod-explorer-crumb-part';
            crumb.style.cursor = 'pointer';
            crumb.title = partPath;

            if (index === parts.length - 1) {
                crumb.style.color = 'var(--text-primary)';
                crumb.style.cursor = 'default';
            } else {
                crumb.style.color = 'var(--text-secondary)';
                crumb.addEventListener('click', () => this.navigateTo(partPath));
            }

            this.breadcrumb.appendChild(crumb);
        });
    }

    renderError(message) {
        this.tbody.innerHTML = `
            <tr>
                <td colspan="2" style="padding: 20px 12px; text-align: center; color: var(--log-error-color, #ff4444);">
                    ⚠️ ${this.escapeHtml(message)}
                </td>
            </tr>`;
    }

    // ──────────────────────────────────────
    //  Utilities
    // ──────────────────────────────────────

    /**
     * Returns a debounced version of fn that fires after `delay` ms of inactivity.
     */
    _debounce(fn, delay) {
        let timer = null;
        return (...args) => {
            clearTimeout(timer);
            timer = setTimeout(() => fn(...args), delay);
        };
    }

    escapeHtml(str) {
        if (typeof str !== 'string') return '';
        return str
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    destroy() {
        this.closeEditor();
        this._hideContextMenu();
        document.removeEventListener('click', this._hideContextMenuBound);
        document.removeEventListener('contextmenu', this._hideContextMenuBound, true);
        window.removeEventListener('amikoGlobalConnect', this.globalConnectHandler);
        window.removeEventListener('amikoGlobalDisconnect', this.globalDisconnectHandler);
    }
}

// Register Module
AmikoModules.register('explorer', ExplorerModule);
