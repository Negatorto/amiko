/*
 * AMIKO - Desktop Module Launcher
 * Desktop Mode: click sidebar dock icon → open module in WinBox
 * Preserves AmikoModuleRegistry (used by all module client.js)
 * Preserves GridStackManager for Settings layout compat
 */

/* ══════════════════════════════════════════════════════
   MODULE REGISTRY (unchanged — required by all modules)
══════════════════════════════════════════════════════ */
class AmikoModuleRegistry {
    constructor() {
        this.registry = {};
    }

    register(type, ModuleClass) {
        this.registry[type] = ModuleClass;
        console.log(`[AmikoModules] Registered module type: ${type}`);
    }

    createInstance(type, containerElement) {
        if (!this.registry[type]) {
            console.error(`[AmikoModules] Unknown module type: ${type}`);
            return null;
        }
        try {
            return new this.registry[type](containerElement);
        } catch (e) {
            console.error(`[AmikoModules] Failed to instantiate ${type}:`, e);
            return null;
        }
    }
}

window.AmikoModules = new AmikoModuleRegistry();


/* ══════════════════════════════════════════════════════
   WINBOX DESKTOP LAUNCHER
══════════════════════════════════════════════════════ */
class AmikoDesktopLauncher {
    constructor() {
        // Track open windows: type → WinBox instance
        this.openWindows = {};

        // GridStack stub (kept so Settings workspace tab doesn't crash)
        this._initGridStackStub();

        this._initDockButtons();
        this._initConnectionToggle();
        this._initClock();
        this._initBottomBarStatus();
        this._initLayoutUI();       // Settings workspace buttons
        this._initStartupBehavior();
    }

    /* ── GridStack stub (prevents errors from settings.js / drag-drop refs) */
    _initGridStackStub() {
        // Minimal stub so settings layout code doesn't crash
        if (window.GridStack) {
            try {
                // Init hidden grid to keep GridStack APIs alive
                this.grid = GridStack.init({
                    column: 12,
                    cellHeight: '130px',
                    margin: 10,
                    acceptWidgets: false,
                    removable: false,
                    float: false,
                }, '#amikoGrid');
            } catch(e) {
                console.warn('[Launcher] GridStack stub init failed:', e);
                this.grid = null;
            }
        } else {
            this.grid = null;
        }
    }

    /* ── Sidebar Dock Buttons ─────────────────── */
    _initDockButtons() {
        const dock = document.getElementById('sidebarDock');
        if (!dock) return;

        dock.addEventListener('click', (e) => {
            const btn = e.target.closest('.dock-item');
            if (!btn) return;
            const type = btn.getAttribute('data-module-type');
            if (type) this.openModule(type, btn);
        });
    }

    /* ── Open Module in WinBox ────────────────── */
    openModule(type, dockBtn = null) {
        // If already open, focus it
        if (this.openWindows[type] && this.openWindows[type]._winbox) {
            try {
                this.openWindows[type]._winbox.focus();
                return;
            } catch(e) {
                // Window was closed externally, continue to reopen
            }
        }

        const templateId = `tpl-${type}`;
        const template = document.getElementById(templateId);
        if (!template) {
            console.error(`[Launcher] Template not found: ${templateId}`);
            return;
        }

        // Clone module content
        const clone = template.content.cloneNode(true);
        const moduleWrapper = clone.querySelector('.amiko-module');
        if (!moduleWrapper) {
            console.error(`[Launcher] .amiko-module not found in template: ${templateId}`);
            return;
        }

        // Get module title from template header
        const titleEl = moduleWrapper.querySelector('.module-title');
        const titleText = titleEl ? titleEl.textContent.trim() : type.toUpperCase();

        // Calculate centered position accounting for sidebar and bars
        const desktopW = window.innerWidth - 60;     // minus sidebar
        const desktopH = window.innerHeight - 52 - 36; // minus topbar + bottombar
        const winW = 700;
        const winH = 480;
        const centerX = 60 + Math.max(0, Math.floor((desktopW - winW) / 2));
        const centerY = 52 + Math.max(0, Math.floor((desktopH - winH) / 2));

        // Tiny cascade so multiple windows don't sit exactly on top of each other
        const openCount = Object.keys(this.openWindows).length;
        const cascadeX = centerX + openCount * 20;
        const cascadeY = centerY + openCount * 20;

        // Create a container div for WinBox to mount
        const mountEl = document.createElement('div');
        mountEl.style.cssText = 'width:100%;height:100%;overflow:hidden;';
        mountEl.appendChild(moduleWrapper);

        // Build WinBox class list from UI prefs (buttons visibility)
        const wbClasses = ['wb-amiko'];
        const enabledBtns = new Set((window.amikoUiPrefs?.winbox_buttons) || ['min', 'max', 'full', 'close']);
        if (!enabledBtns.has('min'))  wbClasses.push('no-min');
        if (!enabledBtns.has('max'))  wbClasses.push('no-max');
        if (!enabledBtns.has('full')) wbClasses.push('no-full');

        const wb = new WinBox({
            title: titleText,
            class: wbClasses,
            mount: mountEl,
            width: 700,
            height: 480,
            x: cascadeX,
            y: cascadeY,
            top: 52,       // below topbar
            bottom: 36,    // above bottombar
            left: 60,      // right of sidebar dock
            index: 100,
            onclose: () => {
                // Destroy module instance
                if (moduleWrapper._amikoInstance && typeof moduleWrapper._amikoInstance.destroy === 'function') {
                    try { moduleWrapper._amikoInstance.destroy(); } catch(e) {}
                }
                // Remove from tracking
                delete this.openWindows[type];
                // Update dock button state
                if (dockBtn) dockBtn.classList.remove('active');
                // Update desktop empty state
                this._updateDesktopEmptyState();
            }
        });


        // Store reference
        this.openWindows[type] = { _winbox: wb, wrapper: moduleWrapper };

        // Initialize module instance
        const instance = window.AmikoModules.createInstance(type, moduleWrapper);
        moduleWrapper._amikoInstance = instance;

        // Mark dock button as active
        if (dockBtn) dockBtn.classList.add('active');

        // Update desktop empty state
        this._updateDesktopEmptyState();
    }

    /* ── Desktop empty state ──────────────────── */
    _updateDesktopEmptyState() {
        const desktop = document.getElementById('amikoDesktop');
        const msg = document.getElementById('emptyDesktopMessage');
        if (!desktop || !msg) return;

        const hasWindows = Object.keys(this.openWindows).length > 0;
        msg.style.opacity = hasWindows ? '0' : '1';
    }

    /* ── Connection Popover Toggle ────────────── */
    _initConnectionToggle() {
        const btn = document.getElementById('connectionToggleBtn');
        const panel = document.getElementById('connectionPanel');
        if (!btn || !panel) return;

        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const isHidden = panel.classList.contains('hidden');
            panel.classList.toggle('hidden', !isHidden);
            btn.setAttribute('aria-expanded', String(isHidden));
        });

        // Close on outside click
        document.addEventListener('click', (e) => {
            if (!panel.contains(e.target) && e.target !== btn) {
                panel.classList.add('hidden');
                btn.setAttribute('aria-expanded', 'false');
            }
        });
    }

    /* ── Live Clock ───────────────────────────── */
    _initClock() {
        const timeEl = document.getElementById('topbar-time');
        const dateEl = document.getElementById('topbar-date');
        if (!timeEl || !dateEl) return;

        this._updateClock = () => {
            const now    = new Date();
            const use12h = window.amikoUiPrefs?.clock_format === '12h';
            timeEl.textContent = now.toLocaleTimeString('it-IT', {
                hour:   '2-digit',
                minute: '2-digit',
                hour12: use12h
            });
            dateEl.textContent = now.toLocaleDateString('it-IT', {
                weekday: 'short', day: '2-digit', month: 'short'
            });
        };

        this._updateClock();
        setInterval(() => this._updateClock(), 10000);
    }


    /* ── Bottom Bar Status Sync ───────────────── */
    _initBottomBarStatus() {
        const dot = document.getElementById('bottombarDot');
        const txt = document.getElementById('bottombarStatusText');
        const topDot = document.getElementById('topbarStatusDot');

        const setConnected = (connected, host) => {
            if (dot) dot.classList.toggle('connected', connected);
            if (txt) txt.textContent = connected ? `Connected: ${host}` : 'Disconnected';
            if (topDot) topDot.classList.toggle('connected', connected);
        };

        window.addEventListener('amikoGlobalConnect', (e) => {
            setConnected(true, e.detail?.hostname || '');
            // Close the popover after successful connect
            const panel = document.getElementById('connectionPanel');
            if (panel) panel.classList.add('hidden');
        });

        window.addEventListener('amikoGlobalDisconnect', () => {
            setConnected(false, '');
        });
    }

    /* ══════════════════════════════════════════════
       LAYOUT PERSISTENCE (Settings workspace tab)
       Kept intact so settings.js + existing logic works
    ══════════════════════════════════════════════ */
    _initLayoutUI() {
        try {
            this.layouts = JSON.parse(localStorage.getItem('amiko_layouts')) || {};
        } catch(e) {
            this.layouts = {};
        }
        this.currentLayout = localStorage.getItem('amiko_current_layout') || null;
        this.isLoading = false;

        this.selectorEl       = document.getElementById('layoutSelector');
        this.btnLoad          = document.getElementById('btnLoadLayout');
        this.btnSave          = document.getElementById('btnSaveLayout');
        this.btnClr           = document.getElementById('btnClearGrid');
        this.btnDelLayout     = document.getElementById('btnDeleteLayout');
        this.startupSelector  = document.getElementById('startupBehaviorSelector');
        this.lockToggle       = document.getElementById('lockWorkspaceToggle');

        this._updateSelectorDropdown();
        this._updateStartupDropdown();

        if (this.btnLoad) {
            this.btnLoad.addEventListener('click', () => {
                const name = this.selectorEl?.value;
                if (name) {
                    this._loadLayout(name);
                    if (window.AmikoAlert) window.AmikoAlert('Workspace', `Layout '${name}' loaded.`, 'success');
                } else {
                    if (window.AmikoAlert) window.AmikoAlert('Workspace', 'Select a layout first.', 'warning');
                }
            });
        }

        if (this.btnSave) {
            this.btnSave.addEventListener('click', () => {
                const onSave = (name) => {
                    if (name) {
                        this.currentLayout = name;
                        this._saveCurrentLayout();
                        this._updateSelectorDropdown();
                        this._updateStartupDropdown();
                        if (window.AmikoAlert) window.AmikoAlert('Success', `Layout '${name}' saved.`, 'success');
                    }
                };
                if (window.AmikoPrompt) {
                    window.AmikoPrompt('Save Layout', 'Enter a name for this layout:', 'My Dashboard', onSave);
                } else {
                    onSave(prompt('Enter a name:', 'My Dashboard'));
                }
            });
        }

        if (this.btnClr) {
            this.btnClr.addEventListener('click', () => {
                const doClear = () => this._closeAllWindows();
                if (window.AmikoConfirm) {
                    window.AmikoConfirm('Warning', 'Close all open module windows?', doClear);
                } else if (confirm('Close all open module windows?')) {
                    doClear();
                }
            });
        }

        if (this.btnDelLayout) {
            this.btnDelLayout.addEventListener('click', () => {
                const name = this.selectorEl?.value;
                if (!name) {
                    if (window.AmikoAlert) window.AmikoAlert('Workspace', 'Select a layout to delete.', 'warning');
                    return;
                }
                const doDelete = () => {
                    delete this.layouts[name];
                    localStorage.setItem('amiko_layouts', JSON.stringify(this.layouts));
                    if (this.currentLayout === name) {
                        this.currentLayout = null;
                        localStorage.removeItem('amiko_current_layout');
                    }
                    this._updateSelectorDropdown();
                    this._updateStartupDropdown();
                    if (window.AmikoAlert) window.AmikoAlert('Success', `Layout '${name}' deleted.`, 'success');
                };
                if (window.AmikoConfirm) {
                    window.AmikoConfirm('Confirm Delete', `Delete layout '${name}'?`, doDelete);
                } else if (confirm(`Delete layout '${name}'?`)) {
                    doDelete();
                }
            });
        }

        if (this.lockToggle) {
            // Lock toggle no longer applies to GridStack, keep UI only
            const isLocked = localStorage.getItem('amiko_grid_locked') === 'true';
            this.lockToggle.checked = isLocked;
            this.lockToggle.addEventListener('change', (e) => {
                localStorage.setItem('amiko_grid_locked', e.target.checked ? 'true' : 'false');
            });
        }

        if (this.startupSelector) {
            this.startupSelector.addEventListener('change', (e) => {
                localStorage.setItem('amiko_startup_behavior', e.target.value);
            });
        }
    }

    _initStartupBehavior() {
        setTimeout(() => {
            const behavior = localStorage.getItem('amiko_startup_behavior') || 'empty';
            if (behavior === 'empty') return;
            if (behavior === 'last' && this.currentLayout && this.layouts[this.currentLayout]) {
                this._loadLayout(this.currentLayout);
            } else if (this.layouts[behavior]) {
                this._loadLayout(behavior);
            }
        }, 200);
    }

    _closeAllWindows() {
        // Close all WinBox windows
        Object.values(this.openWindows).forEach(entry => {
            try { entry._winbox.close(); } catch(e) {}
        });
        this.openWindows = {};
        this._updateDesktopEmptyState();

        // Reset dock buttons
        document.querySelectorAll('.dock-item.active').forEach(b => b.classList.remove('active'));
    }

    _saveCurrentLayout() {
        if (!this.currentLayout) return;
        // Save which modules are currently open
        const layout = Object.keys(this.openWindows).map(type => ({ type }));
        this.layouts[this.currentLayout] = layout;
        localStorage.setItem('amiko_layouts', JSON.stringify(this.layouts));
        localStorage.setItem('amiko_current_layout', this.currentLayout);
    }

    _loadLayout(name) {
        if (!this.layouts[name]) return;
        this.isLoading = true;
        this._closeAllWindows();
        this.currentLayout = name;
        localStorage.setItem('amiko_current_layout', name);

        const layout = this.layouts[name];
        layout.forEach(config => {
            if (config.type) {
                const dockBtn = document.getElementById(`dock-btn-${config.type}`);
                this.openModule(config.type, dockBtn);
            }
        });

        if (this.selectorEl) this.selectorEl.value = name;
        this.isLoading = false;
    }

    _updateSelectorDropdown() {
        if (!this.selectorEl) return;
        this.selectorEl.innerHTML = '<option value="" disabled>-- Select Layout --</option>';
        for (const name in this.layouts) {
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name;
            if (name === this.currentLayout) opt.selected = true;
            this.selectorEl.appendChild(opt);
        }
    }

    _updateStartupDropdown() {
        if (!this.startupSelector) return;
        this.startupSelector.innerHTML = `
            <option value="empty">Start with an Empty Dashboard</option>
            <option value="last">Load Last Active Layout</option>
        `;
        for (const name in this.layouts) {
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = `Load specific layout: ${name}`;
            this.startupSelector.appendChild(opt);
        }
        const current = localStorage.getItem('amiko_startup_behavior') || 'empty';
        this.startupSelector.value = current;
    }

    /* Public alias for old GridStackManager API (settings.js compatibility) */
    saveCurrentLayout()    { this._saveCurrentLayout(); }
    loadLayout(name)       { this._loadLayout(name); }
    clearGrid(save = true) { this._closeAllWindows(); if(save) this._saveCurrentLayout(); }
    updateEmptyState()     { this._updateDesktopEmptyState(); }
}

/* ══════════════════════════════════════════════════════
   INIT
══════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        window.AmikoDragDrop = new AmikoDesktopLauncher();
        // Alias for old code that references GridStackManager
        window.AmikoGridStack = window.AmikoDragDrop;
    }, 100);
});
