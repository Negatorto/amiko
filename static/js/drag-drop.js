/*
 * AMIKO - Modular Drag & Drop System (ES6 Object-Oriented)
 * Powered by GridStack.js
 */

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

class GridStackManager {
    constructor() {
        this.grid = null;
        try {
            this.layouts = JSON.parse(localStorage.getItem('amiko_layouts')) || {};
        } catch(e) {
            this.layouts = {};
        }
        this.currentLayout = localStorage.getItem('amiko_current_layout') || null;
        this.isLoading = false;
        
        this.initGrid();
        this.initSidebarDrag();
        this.initGlobalEventDelegation();
        this.initLayoutUI();
    }

    initSidebarDrag() {
        // Use native HTML5 drag and drop
        const draggables = document.querySelectorAll('.module-item');
        draggables.forEach(draggable => {
            draggable.addEventListener('dragstart', (e) => {
                const type = draggable.getAttribute('data-module-type');
                // Store module type in dataTransfer
                e.dataTransfer.setData('application/amiko-module', type);
                e.dataTransfer.effectAllowed = 'copy';
            });
        });

        // Listen for drops on the grid wrapper area
        const wrapperEl = document.querySelector('.amiko-grid-wrapper');
        if (wrapperEl) {
            wrapperEl.addEventListener('dragover', (e) => {
                e.preventDefault(); // Necessary to allow dropping
                e.dataTransfer.dropEffect = 'copy';
            });

            wrapperEl.addEventListener('drop', (e) => {
                e.preventDefault();
                const moduleType = e.dataTransfer.getData('application/amiko-module');
                if (moduleType) {
                    this.instantiateModuleAndAdd(moduleType, null, e);
                    this.saveCurrentLayout(); // Auto-save on drop
                }
            });
        }
    }

    initGrid() {
        if(!window.GridStack) {
            console.error("GridStack missing!");
            return;
        }
        
        this.grid = GridStack.init({
            column: 12, // GridStack requires 12 columns for default CSS to work
            cellHeight: '130px',
            margin: 10,
            acceptWidgets: false, // We handle it manually
            removable: false,
            float: false, // Ensures widgets pack upwards neatly for an organized look
        }, '#amikoGrid');

        // Watch for changes to update the empty state
        this.grid.on('added removed', () => {
            this.updateEmptyState();
        });
        
        // Initial check
        this.updateEmptyState();
    }

    updateEmptyState() {
        const emptyMsg = document.getElementById('emptyGridMessage');
        if (!emptyMsg || !this.grid) return;
        
        const widgets = this.grid.getGridItems();
        if (widgets && widgets.length > 0) {
            emptyMsg.style.opacity = '0';
        } else {
            emptyMsg.style.opacity = '1';
        }
    }

    instantiateModuleAndAdd(type, config = null, dropEvent = null) {
        const templateId = `tpl-${type}`;
        const template = document.getElementById(templateId);

        if (!template) {
            console.error(`[GridStackManager] Template not found for module type: ${type}`);
            return;
        }

        let x = 0, y = 0, w = 6, h = 3;

        // Calculate drop position if dropped from sidebar
        if (dropEvent) {
            const gridEl = document.querySelector('.grid-stack');
            const rect = gridEl.getBoundingClientRect();
            
            const cellWidth = gridEl.offsetWidth / 12;
            const cellHeight = 140; 
            x = Math.floor((dropEvent.clientX - rect.left) / cellWidth);
            y = Math.floor((dropEvent.clientY - rect.top) / cellHeight);

            // Keep bounds
            if (x < 0) x = 0;
            if (x > 6) x = 6;
            if (y < 0) y = 0;
        } else if (config) {
            x = config.x;
            y = config.y;
            w = config.w;
            h = config.h;
        }

        // Build widget DOM
        const widgetEl = document.createElement('div');
        widgetEl.className = 'grid-stack-item';
        widgetEl.setAttribute('data-module-type', type);
        
        if (config && config.isExpanded) widgetEl.setAttribute('data-amiko-expanded', 'true');
        if (config && config.origW) widgetEl.setAttribute('data-orig-w', config.origW);
        if (config && config.origH) widgetEl.setAttribute('data-orig-h', config.origH);
        if (config && config.origX) widgetEl.setAttribute('data-orig-x', config.origX);
        if (config && config.origY) widgetEl.setAttribute('data-orig-y', config.origY);

        // Inner content container
        const contentEl = document.createElement('div');
        contentEl.className = 'grid-stack-item-content';
        
        // Clone module template
        const clone = template.content.cloneNode(true);
        const moduleWrapper = clone.querySelector('.amiko-module');
        if (!moduleWrapper) return;

        // Assemble
        contentEl.appendChild(moduleWrapper);
        widgetEl.appendChild(contentEl);

        // Add to grid programmatically
        this.grid.addWidget(widgetEl, { w, h, x, y, autoPosition: !config });

        // Initialize internal module logic
        const instance = window.AmikoModules.createInstance(type, moduleWrapper);
        moduleWrapper._amikoInstance = instance;
    }

    initGlobalEventDelegation() {
        document.querySelector('.grid-stack').addEventListener('click', (e) => {
            const btnClose = e.target.closest('.btn-close');
            const btnExpand = e.target.closest('.btn-expand');

            if (btnClose || btnExpand) {
                const moduleWrapper = e.target.closest('.amiko-module');
                const widgetEl = e.target.closest('.grid-stack-item');

                if (!moduleWrapper || !widgetEl) return;

                if (btnClose) {
                    this.handleCloseModule(moduleWrapper, widgetEl);
                } else if (btnExpand) {
                    this.handleExpandModule(widgetEl);
                }
            }
        });
    }

    handleCloseModule(moduleWrapper, widgetEl) {
        if (moduleWrapper._amikoInstance && typeof moduleWrapper._amikoInstance.destroy === 'function') {
            try {
                moduleWrapper._amikoInstance.destroy();
            } catch (err) {
                console.error("Error destroying module:", err);
            }
        }
        
        this.grid.removeWidget(widgetEl);
    }

    handleExpandModule(widgetEl) {
        const isExpanded = widgetEl.getAttribute('data-amiko-expanded') === 'true';
        
        if (isExpanded) {
            const origW = parseInt(widgetEl.getAttribute('data-orig-w')) || 6;
            const origH = parseInt(widgetEl.getAttribute('data-orig-h')) || 3;
            const origX = parseInt(widgetEl.getAttribute('data-orig-x')) || 0;
            const origY = parseInt(widgetEl.getAttribute('data-orig-y')) || 0;
            this.grid.update(widgetEl, {w: origW, h: origH, x: origX, y: origY});
            widgetEl.removeAttribute('data-amiko-expanded');
        } else {
            const node = widgetEl.gridstackNode;
            if (node) {
                widgetEl.setAttribute('data-orig-w', node.w);
                widgetEl.setAttribute('data-orig-h', node.h);
                widgetEl.setAttribute('data-orig-x', node.x);
                widgetEl.setAttribute('data-orig-y', node.y);
            }
            this.grid.update(widgetEl, {w: 12, h: 5, x: 0});
            widgetEl.setAttribute('data-amiko-expanded', 'true');
        }
        this.saveCurrentLayout(); // Auto-save layout on modify
    }

    // ── Layout Persistence Methods ──

    initLayoutUI() {
        this.selectorEl = document.getElementById('layoutSelector');
        this.btnLoad = document.getElementById('btnLoadLayout');
        this.btnSave = document.getElementById('btnSaveLayout');
        this.btnClr = document.getElementById('btnClearGrid');
        this.btnDelLayout = document.getElementById('btnDeleteLayout');
        this.startupSelector = document.getElementById('startupBehaviorSelector');
        this.lockToggle = document.getElementById('lockWorkspaceToggle');

        this.updateSelectorDropdown();
        this.updateStartupDropdown();

        if (this.btnLoad) {
            this.btnLoad.addEventListener('click', () => {
                const layoutName = this.selectorEl.value;
                if (layoutName) {
                    this.loadLayout(layoutName);
                    if (window.AmikoAlert) window.AmikoAlert('Workspace', `Workspace '${layoutName}' loaded successfully.`, 'success');
                } else {
                    if (window.AmikoAlert) window.AmikoAlert('Workspace', 'Please select a layout to load from the dropdown.', 'warning');
                }
            });
        }

        if (this.btnSave) {
            this.btnSave.addEventListener('click', () => {
                const onSave = (name) => {
                    if (name) {
                        this.currentLayout = name;
                        this.saveCurrentLayout();
                        this.updateSelectorDropdown();
                        this.updateStartupDropdown();
                        if (window.AmikoAlert) window.AmikoAlert('Success', `Layout '${name}' saved successfully.`, 'success');
                    }
                };
                if (window.AmikoPrompt) {
                    window.AmikoPrompt("Save Layout", "Enter a name for this Layout:", "My Dashboard", onSave);
                } else {
                    const name = prompt("Enter a name for this Layout:", "My Dashboard");
                    onSave(name);
                }
            });
        }

        if (this.btnClr) {
            this.btnClr.addEventListener('click', () => {
                const doClear = () => {
                    this.clearGrid();
                };
                if (window.AmikoConfirm) {
                    window.AmikoConfirm('Warning', 'Are you sure you want to clear the dashboard?', doClear);
                } else if(confirm("Are you sure you want to clear the dashboard?")) {
                    doClear();
                }
            });
        }

        if (this.btnDelLayout) {
            this.btnDelLayout.addEventListener('click', () => {
                const layoutName = this.selectorEl.value;
                if (!layoutName) {
                    if (window.AmikoAlert) window.AmikoAlert('Workspace', 'Please select a layout to delete.', 'warning');
                    else alert('Please select a layout to delete.');
                    return;
                }
                const doDelete = () => {
                    delete this.layouts[layoutName];
                    localStorage.setItem('amiko_layouts', JSON.stringify(this.layouts));
                    
                    if (this.currentLayout === layoutName) {
                        this.currentLayout = null;
                        localStorage.removeItem('amiko_current_layout');
                    }
                    
                    this.updateSelectorDropdown();
                    this.updateStartupDropdown();
                    
                    if (window.AmikoAlert) window.AmikoAlert('Success', `Layout '${layoutName}' deleted.`, 'success');
                };

                if (window.AmikoConfirm) {
                    window.AmikoConfirm('Confirm Delete', `Are you sure you want to delete the layout '${layoutName}'?`, doDelete);
                } else if (confirm(`Are you sure you want to delete the layout '${layoutName}'?`)) {
                    doDelete();
                }
            });
        }

        // Initialize Grid Lock
        if (this.lockToggle) {
            const isLocked = localStorage.getItem('amiko_grid_locked') === 'true';
            this.lockToggle.checked = isLocked;
            this.toggleGridLock(isLocked);
            this.lockToggle.addEventListener('change', (e) => {
                this.toggleGridLock(e.target.checked);
            });
        }

        // Auto-save on change
        this.grid.on('change', () => {
            if (!this.isLoading) this.saveCurrentLayout();
        });

        // Handle Startup Behavior
        setTimeout(() => {
            const startupBehavior = localStorage.getItem('amiko_startup_behavior') || 'empty';
            if (startupBehavior === 'empty') {
                this.clearGrid();
            } else if (startupBehavior === 'last' && this.currentLayout) {
                this.loadLayout(this.currentLayout);
            } else if (this.layouts[startupBehavior]) {
                this.loadLayout(startupBehavior);
            }
        }, 100);

        // Update startup behavior when changed in settings
        if (this.startupSelector) {
            this.startupSelector.addEventListener('change', (e) => {
                localStorage.setItem('amiko_startup_behavior', e.target.value);
            });
        }
    }

    toggleGridLock(isLocked) {
        if (!this.grid) return;
        this.grid.setStatic(isLocked);
        localStorage.setItem('amiko_grid_locked', isLocked ? 'true' : 'false');
        
        // Disable deleting modules if grid is locked
        const gridItems = document.querySelectorAll('.grid-stack-item');
        gridItems.forEach(item => {
            const closeBtn = item.querySelector('.btn-close');
            const expandBtn = item.querySelector('.btn-expand');
            if (closeBtn) closeBtn.style.display = isLocked ? 'none' : 'block';
            if (expandBtn) expandBtn.style.display = isLocked ? 'none' : 'block';
        });
    }

    updateSelectorDropdown() {
        if (!this.selectorEl) return;
        this.selectorEl.innerHTML = '<option value="" disabled>-- Select Layout --</option>';
        for (const layoutName in this.layouts) {
            const opt = document.createElement('option');
            opt.value = layoutName;
            opt.textContent = layoutName;
            if (layoutName === this.currentLayout) opt.selected = true;
            this.selectorEl.appendChild(opt);
        }
    }

    updateStartupDropdown() {
        if (!this.startupSelector) return;
        // Keep the first two fixed options
        this.startupSelector.innerHTML = `
            <option value="empty">Start with an Empty Dashboard</option>
            <option value="last">Load Last Active Layout</option>
        `;
        // Append user layouts
        for (const layoutName in this.layouts) {
            const opt = document.createElement('option');
            opt.value = layoutName;
            opt.textContent = `Load specific layout: ${layoutName}`;
            this.startupSelector.appendChild(opt);
        }
        // Set selected
        const currentStartup = localStorage.getItem('amiko_startup_behavior') || 'empty';
        this.startupSelector.value = currentStartup;
    }
    saveCurrentLayout() {
        if (!this.grid || this.isLoading) return;
        
        const serialized = this.grid.getGridItems().map(el => {
            const node = el.gridstackNode;
            if (!node) return null;
            return {
                x: node.x,
                y: node.y,
                w: node.w,
                h: node.h,
                type: el.getAttribute('data-module-type'),
                isExpanded: el.getAttribute('data-amiko-expanded') === 'true',
                origW: el.getAttribute('data-orig-w'),
                origH: el.getAttribute('data-orig-h'),
                origX: el.getAttribute('data-orig-x'),
                origY: el.getAttribute('data-orig-y')
            };
        }).filter(Boolean);
        
        // Don't auto-save an empty layout if there's no defined name
        if (!this.currentLayout) {
            // Generate a default name if it's the first time
            if (serialized.length > 0) {
                this.currentLayout = "AutoSave";
            } else {
                return; // Nothing to save
            }
        }
        
        this.layouts[this.currentLayout] = serialized;
        localStorage.setItem('amiko_layouts', JSON.stringify(this.layouts));
        localStorage.setItem('amiko_current_layout', this.currentLayout);
    }

    loadLayout(name) {
        if (!this.layouts[name]) return;
        
        this.isLoading = true;
        
        // Copy data to prevent referencing issues
        const layoutData = Array.from(this.layouts[name]);

        // Clear existing grid without triggering an auto-save that would overwrite the newly selected name
        this.clearGrid(false);
        
        this.currentLayout = name;
        localStorage.setItem('amiko_current_layout', this.currentLayout);

        layoutData.forEach(config => {
            if (config.type) {
                this.instantiateModuleAndAdd(config.type, config, null);
            }
        });
        
        // Ensure selector reflects current
        if (this.selectorEl) this.selectorEl.value = this.currentLayout;
        
        this.isLoading = false;
        // Save the freshly rendered grid if needed
        this.saveCurrentLayout();
    }

    clearGrid(saveAfter = true) {
        if (!this.grid) return;
        const widgets = this.grid.getGridItems();
        widgets.forEach(widget => {
            const moduleWrapper = widget.querySelector('.amiko-module');
            this.handleCloseModule(moduleWrapper, widget);
        });
        this.updateEmptyState();
        if (saveAfter) {
            this.saveCurrentLayout();
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        window.AmikoDragDrop = new GridStackManager();
    }, 100);
});
