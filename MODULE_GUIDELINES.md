# AMIKO — Module Development Guide

This guide explains how to create a new module for AMIKO.

AMIKO uses a **plugin architecture**: each module lives in its own folder under `modules/`. The engine discovers and loads them automatically at startup — **no changes to the core are needed**.

When a user clicks a module icon in the sidebar dock, AMIKO opens a floating WinBox window and mounts your module's template inside it.

---

## Anatomy of a Module

```
modules/
└── my_module/          ← folder name = module ID (use snake_case)
    ├── module.json     ← required: metadata and icon
    ├── template.html   ← required: UI layout
    ├── client.js       ← required: frontend logic
    └── backend.py      ← optional: server-side API routes
```

---

## Step 1 — `module.json`

```json
{
    "name": "My Module",
    "icon": "<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M12 2L2 22h20L12 2z'/></svg>",
    "enabled": true,
    "index": 50,
    "description": "A short description of what this module does.",
    "author": "Your Name"
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `name` | ✅ | Display name shown in the sidebar tooltip |
| `icon` | ✅ | SVG string. AMIKO converts it to a sprite symbol you can reference as `#icon-my_module` |
| `enabled` | ✅ | Set to `false` to hide it from the dock without deleting it |
| `index` | ✅ | Controls the vertical order in the sidebar. Lower = higher up |
| `description` | — | Internal reference only |
| `author` | — | Optional credit |

---

## Step 2 — `template.html`

This is the UI that gets mounted inside the WinBox window body.

> ⚠️ **Never use `id="..."` attributes.** Multiple instances of your module can be open at the same time. Always use classes and scope queries to `this.container`.

```html
<!-- Root container — data-module-type MUST match your folder name -->
<div class="amiko-module" data-module-type="my_module">

    <!-- Title bar (shown inside WinBox body, below WinBox's own header) -->
    <div class="module-header">
        <span class="module-title">
            <svg class="module-icon-svg"><use href="#icon-my_module"></use></svg>
            My Module
        </span>
    </div>

    <!-- Content area -->
    <div class="module-body">

        <!-- Example: a form -->
        <form class="form-grid amiko-mod-form">
            <div class="form-group">
                <label>Remote Path</label>
                <input type="text" class="mod-my-path" placeholder="/var/www" required>
            </div>
            <div class="form-group">
                <label>&nbsp;</label>
                <button type="submit" class="mod-btn-submit">RUN</button>
            </div>
        </form>

        <!-- Example: a log terminal -->
        <div class="terminal-container" style="flex: 1;">
            <div class="terminal-body large mod-my-terminal" style="height: 100%;">
                <div class="log-entry log-dim">// Ready...</div>
            </div>
        </div>

    </div>
</div>
```

### Useful CSS classes

| Class | Purpose |
|-------|---------|
| `amiko-module` | Root wrapper — required |
| `module-header` / `module-title` | Styled title bar inside the module body |
| `module-body` | Flex column content area |
| `form-grid amiko-mod-form` | Responsive form layout |
| `form-group` | Label + input pair |
| `mod-btn-submit` | Primary action button (glowing style) |
| `terminal-container` | Wrapper for log output |
| `terminal-body large` | Scrollable log area |
| `log-entry log-dim` | Faded initial log line |

### CSS variables (always use these, never hardcode colors)

| Variable | Color |
|----------|-------|
| `var(--bg-primary)` | Main background |
| `var(--bg-secondary)` | Panels / bars |
| `var(--text-primary)` | Primary text (theme accent) |
| `var(--text-secondary)` | Dimmer text |
| `var(--text-dim)` | Very faint text / borders |
| `var(--accent-warning)` | Yellow/amber warnings |
| `var(--accent-danger)` | Red errors |
| `var(--glow-primary)` | Box-shadow glow color |

---

## Step 3 — `client.js`

AMIKO automatically injects this script into the browser. You must define a class and register it with `AmikoModules.register`.

```javascript
/*
 * AMIKO - My Module
 */
class MyModule {
    constructor(container) {
        // 'container' is the .amiko-module DOM element for THIS instance.
        // Always scope your queries to this.container — never use document.querySelector.
        this.container = container;

        // Find your elements
        this.form     = container.querySelector('.amiko-mod-form');
        this.terminal = container.querySelector('.mod-my-terminal');

        // Bind events
        if (this.form) {
            this.form.addEventListener('submit', (e) => {
                e.preventDefault();
                this.run();
            });
        }

        // React to global SSH connection events
        this._onConnect    = this.onGlobalConnect.bind(this);
        this._onDisconnect = this.onGlobalDisconnect.bind(this);
        window.addEventListener('amikoGlobalConnect',    this._onConnect);
        window.addEventListener('amikoGlobalDisconnect', this._onDisconnect);

        // If already connected when the window opens, initialise immediately
        if (window.globalConnection?.connected) {
            this.onGlobalConnect();
        }
    }

    onGlobalConnect() {
        addLogEntry(this.terminal, 'Connected — ready.', 'success');
    }

    onGlobalDisconnect() {
        addLogEntry(this.terminal, 'Disconnected.', 'error');
    }

    async run() {
        if (!window.globalConnection?.connected) {
            window.AmikoAlert('Error', 'Connect to a server first.');
            return;
        }

        const path = this.container.querySelector('.mod-my-path').value;

        addLogEntry(this.terminal, `Running on ${window.globalConnection.hostname}…`, 'info');

        try {
            const res  = await fetch('/my-module/do-something', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({
                    hostname: window.globalConnection.hostname,
                    port:     window.globalConnection.port,
                    username: window.globalConnection.username,
                    password: window.globalConnection.password,
                    path
                })
            });
            const data = await res.json();

            if (data.status === 'ok') {
                addLogEntry(this.terminal, data.message, 'success');
            } else {
                addLogEntry(this.terminal, `Error: ${data.message}`, 'error');
            }
        } catch (err) {
            addLogEntry(this.terminal, `Network error: ${err.message}`, 'error');
        }
    }

    // Called automatically when the user closes the WinBox window.
    // Clean up listeners and any open connections/intervals here.
    destroy() {
        window.removeEventListener('amikoGlobalConnect',    this._onConnect);
        window.removeEventListener('amikoGlobalDisconnect', this._onDisconnect);
    }
}

// The string MUST match your folder name exactly.
AmikoModules.register('my_module', MyModule);
```

---

## Step 4 — `backend.py` (optional)

Only needed if your module requires server-side logic (SSH commands, file access, etc.).

```python
from flask import Blueprint, request, jsonify
from utils.sftp_helper import connect_sftp

# The Blueprint object MUST be named 'blueprint'.
blueprint = Blueprint('my_module', __name__)

@blueprint.route('/my-module/do-something', methods=['POST'])
def do_something():
    data     = request.get_json() or {}
    hostname = data.get('hostname')
    port     = int(data.get('port', 22))
    username = data.get('username')
    password = data.get('password')
    path     = data.get('path', '/')

    if not all([hostname, username, password]):
        return jsonify({'status': 'error', 'message': 'Missing credentials.'}), 400

    try:
        ssh, sftp = connect_sftp(hostname, port, username, password)
        # ... do your work ...
        sftp.close()
        ssh.close()
        return jsonify({'status': 'ok', 'message': f'Done on {hostname}:{path}'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
```

---

## Global Utilities Reference

These are available everywhere in your `client.js`.

### Connection state

```javascript
window.globalConnection.connected  // boolean
window.globalConnection.hostname   // string
window.globalConnection.port       // string
window.globalConnection.username   // string
window.globalConnection.password   // string
```

### Current user & RBAC capabilities

AMIKO exposes the authenticated user and their role capabilities globally via `window.amikoCurrentUser`:

```javascript
window.amikoCurrentUser.username                       // string
window.amikoCurrentUser.role                           // 'admin' | 'operator' | 'viewer'
window.amikoCurrentUser.allowed_groups                 // string[] (group IDs, or ['*'] for admin)
window.amikoCurrentUser.capabilities.can_write         // boolean (false for 'viewer')
window.amikoCurrentUser.capabilities.can_ssh           // boolean (false for 'viewer')
window.amikoCurrentUser.capabilities.can_manage_settings // boolean (true only for 'admin')
```

If your module performs write or destructive operations (saving files, modifying data, deleting entries), always check `window.amikoCurrentUser?.capabilities?.can_write !== false` in `client.js` to hide or disable write controls for `viewer` users, and enforce the same check on your backend endpoints.

### Log helpers

```javascript
addLogEntry(terminalEl, 'Message', 'success'); // type: log | info | success | error | match | dim
clearTerminal(terminalEl);
scrollTerminalToBottom(terminalEl);
formatTimestamp(); // returns localized date/time string
```

### Dialog windows

```javascript
// Alert (type: 'error' | 'success' | 'warning')
window.AmikoAlert('Title', 'Message', 'error');

// Confirmation — onConfirm() called if user clicks Yes
window.AmikoConfirm('Title', 'Are you sure?', () => { /* confirmed */ });

// Prompt — onConfirm(value) called with the entered string
window.AmikoPrompt('Title', 'Enter a name:', 'default', (value) => { /* use value */ });
```

### Opening a sub-popup from inside a module

```javascript
const popup = new WinBox({
    title: 'My Popup',
    class: ['wb-amiko'],
    html: '<div style="padding:20px;">Content</div>',
    width:  500,
    height: 350,
    x: 'center',
    y: 'center',
    top:    52,   // below AMIKO top bar
    bottom: 36,   // above AMIKO bottom bar
    left:   60,   // right of sidebar dock
    index:  9999
});
```

---

## SocketIO (real-time streaming)

Use this when your module needs live data pushed from the server (e.g. a log tail, live stats).

**`client.js`**
```javascript
constructor(container) {
    // ...
    this.socket = io({ forceNew: true });

    this.socket.on('my_module_output', (data) => {
        addLogEntry(this.terminal, data.line, 'log');
    });
}

startStreaming() {
    this.socket.emit('my_module_start', {
        session_id: window.globalConnection.sessionId
    });
}

destroy() {
    if (this.socket) {
        this.socket.disconnect();
        this.socket = null;
    }
}
```

**`core/handlers.py`** (SocketIO events are registered in the core handlers module)
```python
@socketio.on('my_module_start')
def handle_my_module_start(data):
    # stream results back
    emit('my_module_output', {'line': 'Hello from server'}, to=request.sid)
```

---

## Step 5 — Module-specific Settings (Interface tab)

If your module exposes user-configurable options (e.g. a size limit, a timeout, a display preference), you can add them to **Settings → Interface** in a way that is automatically hidden when the module is removed or disabled.

### How it works

The settings panel reads `GET /api/settings/ui`, which returns the list of currently active module IDs in the `active_modules` field. Any `<div>` in the Interface section marked with `data-requires-module="your_module_id"` is automatically hidden if `your_module_id` is not in that list.

### Adding a setting group

**1. Add the HTML block to `templates/index.html`** inside the `<div data-section="interface">` section:

```html
<!-- Your module's settings group -->
<div class="iface-pref-group" data-requires-module="my_module">
    <div class="iface-pref-header">
        <span class="iface-pref-icon">🔧</span>
        <div>
            <div class="iface-pref-title">My Module — Option Name</div>
            <div class="iface-pref-desc">A short description of what this setting does.</div>
        </div>
    </div>
    <!-- Your controls here (slider, select, checkboxes…) -->
    <div class="iface-slider-row">
        <span class="iface-slider-label">Min</span>
        <input type="range" id="myModuleOption" min="1" max="100" step="1" value="50" class="iface-slider">
        <span class="iface-slider-label">Max</span>
        <span class="iface-slider-value" id="myModuleOptionValue">50</span>
    </div>
</div>
```

> ⚠️ The `data-requires-module` value **must exactly match your module's folder name** (the same ID used in `module.json` and `AmikoModules.register()`).

**2. Wire up the control in `static/js/settings.js`**, inside `initInterface()` (live feedback) and `loadInterface()` / `saveInterface()` (load & persist):

```javascript
// initInterface() — live feedback
const slider = document.getElementById('myModuleOption');
const sliderVal = document.getElementById('myModuleOptionValue');
if (slider && sliderVal) {
    slider.addEventListener('input', () => {
        sliderVal.textContent = slider.value;
    });
}

// _applyInterfaceFormValues(prefs) — populate from saved value
const s = document.getElementById('myModuleOption');
if (s) {
    s.value = prefs.my_module_option ?? 50;
    document.getElementById('myModuleOptionValue').textContent = s.value;
}

// saveInterface() — collect & send
const my_module_option = parseInt(document.getElementById('myModuleOption')?.value ?? 50);
// Add to the PUT /api/settings/ui payload:
await this.api('PUT', '/api/settings/ui', { my_module_option, /* …other fields */ });
```

**3. Persist the value in `utils/settings_manager.py`**, inside `_UI_DEFAULTS` and `set_ui_prefs()`:

```python
_UI_DEFAULTS = {
    # …existing defaults…
    "my_module_option": 50,
}

def set_ui_prefs(self, prefs: dict):
    # …existing validation…
    if "my_module_option" in prefs:
        current["my_module_option"] = max(1, min(100, int(prefs["my_module_option"])))
    self._settings["ui_prefs"] = current
    self._save()
    return current
```

**4. Read it in your `backend.py`:**

```python
from core.config import settings as _amiko_settings

def _get_my_option():
    return _amiko_settings.get_ui_prefs().get('my_module_option', 50)
```

### Key rules

| Rule | Reason |
|------|--------|
| Always add `data-requires-module="your_id"` | Group hides automatically when module is absent |
| Use `iface-pref-group` / `iface-pref-header` CSS classes | Consistent look with the rest of the Interface tab |
| Read the value at request time (not at startup) | Setting changes take effect immediately, no restart needed |

---

## Step 6 — Role-Based Access Control (RBAC) in Backend Routes

All requests to AMIKO are automatically authenticated by `core/auth.py`. When writing custom endpoints in `backend.py`, keep the following Least Privilege rules in mind:

1. **Restricting write endpoints to `admin` and `operator`:**
   Use `@role_required('admin', 'operator')` on routes that modify remote files, execute commands, or alter persistent state so `viewer` users remain strictly read-only.
2. **Validating server group access:**
   Always verify that non-admin users have access to the target server's group via `settings.can_user_access_server`:

```python
from flask import Blueprint, request, jsonify
from core.auth import get_current_user, role_required
from core.config import settings

blueprint = Blueprint('my_module', __name__)

@blueprint.route('/my-module/write-action', methods=['POST'])
@role_required('admin', 'operator')
def write_action():
    user = get_current_user()
    data = request.get_json() or {}
    hostname = data.get('hostname')
    port = data.get('port', 22)

    if not settings.can_user_access_server(user, hostname=hostname, port=port):
        return jsonify({'status': 'error', 'message': 'Access to this server is not permitted for your assigned groups.'}), 403

    # Proceed with operation...
    return jsonify({'status': 'ok'})
```

---

## Checklist before submitting a module

- [ ] Folder name is `snake_case` and matches the ID used in `module.json`, `template.html`, and `AmikoModules.register()`
- [ ] No `id="..."` in `template.html` — classes only
- [ ] All DOM queries scoped to `this.container.querySelector(...)`
- [ ] `destroy()` method removes all event listeners and closes sockets/intervals
- [ ] Colors use CSS variables (`var(--text-primary)`, etc.) — no hardcoded hex values
- [ ] `blueprint` object is exported in `backend.py` (if present)
- [ ] Write/destructive actions check `window.amikoCurrentUser?.capabilities?.can_write` in `client.js` and enforce `@role_required` / `settings.can_user_access_server` in `backend.py`
- [ ] Any Interface tab settings group uses `data-requires-module="your_id"` so it disappears when the module is removed
