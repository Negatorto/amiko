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
        this.tbody = container.querySelector('.mod-explorer-tbody');
        this.loadingOverlay = container.querySelector('.mod-explorer-loading');
        this.crumbRoot = container.querySelector('.mod-explorer-crumb-root');

        // Editor popup state
        this.editorPopup = null;
        this.editorTextarea = null;
        this.editorLineNumbers = null;
        this.currentFilePath = null;
        this.originalContent = '';
        this.isModified = false;

        // Bind events
        if (this.btnGo) {
            this.btnGo.addEventListener('click', () => {
                const path = this.pathInput.value.trim() || '/';
                this.navigateTo(path);
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

        // Grid span override removed in favor of fluid CSS Grid
        this.gridCell = this.container.closest('.grid-cell');

        // Global connection events
        this.globalConnectHandler = this.onGlobalConnect.bind(this);
        this.globalDisconnectHandler = this.onGlobalDisconnect.bind(this);
        window.addEventListener('amikoGlobalConnect', this.globalConnectHandler);
        window.addEventListener('amikoGlobalDisconnect', this.globalDisconnectHandler);

        // If already connected, navigate to root immediately
        if (window.globalConnection && window.globalConnection.connected) {
            this.navigateTo('/');
        }
    }

    onGlobalConnect() {
        this.navigateTo('/');
    }

    onGlobalDisconnect() {
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

    buildQueryParams(extraParams) {
        if (!window.globalConnection || !window.globalConnection.connected) {
            return null;
        }
        const params = new URLSearchParams();
        params.append('hostname', window.globalConnection.hostname);
        params.append('port', window.globalConnection.port);
        params.append('username', window.globalConnection.username);
        params.append('password', window.globalConnection.password);

        if (extraParams) {
            for (const [key, value] of Object.entries(extraParams)) {
                params.append(key, value);
            }
        }
        return params;
    }

    // ──────────────────────────────────────
    //  Directory Navigation
    // ──────────────────────────────────────

    async navigateTo(path) {
        if (!window.globalConnection || !window.globalConnection.connected) {
            if (window.AmikoAlert) window.AmikoAlert('Error', 'You must connect first using the global connection form!');
            return;
        }

        path = path.replace(/\/+/g, '/') || '/';
        if (!path.startsWith('/')) path = '/' + path;

        this.showLoading('⏳ Loading directory...');

        const params = this.buildQueryParams({ path: path });
        if (!params) return;

        try {
            const response = await fetch('/explorer/list-dir?' + params.toString());
            const data = await response.json();

            if (data.status === 'ok') {
                this.currentPath = data.path;
                this.pathInput.value = data.path;
                this.renderBreadcrumb(data.path);
                this.renderEntries(data.entries);
            } else {
                this.renderError(data.message || 'Unknown error.');
            }
        } catch (err) {
            this.renderError('Network error: ' + err.message);
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

        const params = this.buildQueryParams({ path: path });
        if (!params) return;

        try {
            const response = await fetch('/explorer/read-file?' + params.toString());
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

        // Right side: buttons
        const buttons = document.createElement('div');
        buttons.style.cssText = 'display: flex; gap: 8px; flex-shrink: 0; align-items: center;';

        const btnSave = document.createElement('button');
        btnSave.textContent = '💾 SAVE';
        btnSave.style.cssText = `
            background: rgba(0, 180, 80, 0.15); border: 1px solid rgba(0, 180, 80, 0.4);
            color: #4cff88; cursor: pointer; padding: 5px 16px; border-radius: 4px;
            font-family: monospace; font-size: 0.85em; transition: all 0.2s;
        `;
        btnSave.addEventListener('mouseenter', () => { btnSave.style.background = 'rgba(0, 180, 80, 0.3)'; });
        btnSave.addEventListener('mouseleave', () => { btnSave.style.background = 'rgba(0, 180, 80, 0.15)'; });
        btnSave.addEventListener('click', () => this.saveFile());
        buttons.appendChild(btnSave);

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
            this.updateLineNumbers(textarea.value);
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

        const params = this.buildQueryParams({});
        if (!params) return;

        try {
            const response = await fetch('/explorer/save-file', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    hostname: window.globalConnection.hostname,
                    port: window.globalConnection.port,
                    username: window.globalConnection.username,
                    password: window.globalConnection.password,
                    path: this.currentFilePath,
                    content: content
                })
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
        const params = this.buildQueryParams({ path: path });
        if (!params) return;

        try {
            const response = await fetch('/explorer/read-image?' + params.toString());
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
    //  File Listing Rendering
    // ──────────────────────────────────────

    renderEntries(entries) {
        this.tbody.innerHTML = '';

        // Parent directory row (go up)
        if (this.currentPath !== '/') {
            const upRow = document.createElement('tr');
            upRow.style.cssText = 'cursor: pointer; border-bottom: 1px solid var(--border-medium, rgba(255,255,255,0.05));';
            upRow.innerHTML = `
                <td style="padding: 7px 12px; color: var(--text-primary);">📁 ..</td>
                <td style="padding: 7px 12px; text-align: right; color: var(--text-dim);">—</td>`;
            upRow.addEventListener('dblclick', () => {
                const parentPath = this.currentPath.replace(/\/[^/]+\/?$/, '') || '/';
                this.navigateTo(parentPath);
            });
            this.applyRowHover(upRow);
            this.tbody.appendChild(upRow);
        }

        if (entries.length === 0) {
            const emptyRow = document.createElement('tr');
            emptyRow.innerHTML = `
                <td colspan="2" style="padding: 20px 12px; text-align: center; opacity: 0.4;">
                    // Directory is empty
                </td>`;
            this.tbody.appendChild(emptyRow);
            return;
        }

            entries.forEach(entry => {
            const row = document.createElement('tr');
            row.style.cssText = 'cursor: pointer; border-bottom: 1px solid var(--border-medium, rgba(255,255,255,0.05));';

            const icon = entry.is_dir ? '📁' : '📄';

            row.innerHTML = `
                <td style="padding: 7px 12px; color: var(--text-primary);">${icon} ${this.escapeHtml(entry.name)}</td>
                <td style="padding: 7px 12px; text-align: right; color: var(--text-dim);">${entry.size_display}</td>`;

            row.addEventListener('dblclick', () => {
                const fullPath = this.currentPath === '/'
                    ? '/' + entry.name
                    : this.currentPath + '/' + entry.name;

                if (entry.is_dir) {
                    this.navigateTo(fullPath);
                } else {
                    const ext = entry.name.split('.').pop().toLowerCase();
                    const imageExts = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'ico'];
                    if (imageExts.includes(ext)) {
                        this.openImage(fullPath, entry.name);
                    } else {
                        this.openFile(fullPath);
                    }
                }
            });

            this.applyRowHover(row);
            this.tbody.appendChild(row);
        });
    }

    applyRowHover(row) {
        row.addEventListener('mouseenter', () => {
            row.style.background = 'var(--glow-primary, rgba(255,255,255,0.05))';
        });
        row.addEventListener('mouseleave', () => {
            row.style.background = 'transparent';
        });
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

    escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    destroy() {
        this.closeEditor();
        window.removeEventListener('amikoGlobalConnect', this.globalConnectHandler);
        window.removeEventListener('amikoGlobalDisconnect', this.globalDisconnectHandler);
    }
}

// Register Module
AmikoModules.register('explorer', ExplorerModule);
