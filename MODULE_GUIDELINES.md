# AMIKO - Module Development Guidelines

This guide explains how to create a new module to be inserted into the AMIKO Drag & Drop system.
AMIKO uses a **Backend-Driven Plugin Architecture**, meaning each module lives in its own isolated folder within the `modules/` directory. The main AMIKO engine automatically discovers and loads them on startup.

---

## Overview

An AMIKO module is composed of a dedicated folder (e.g., `modules/my_module/`) containing up to 4 specific files:
1. **`module.json`** (Required): Configuration metadata (Name, Icon, Status).
2. **`template.html`** (Required): The UI layout injected into the grid.
3. **`client.js`** (Required): The frontend JavaScript logic that controls the module.
4. **`backend.py`** (Optional): A Flask Blueprint containing isolated API/backend routes for the module.

---

## Step 1: Create the Module Directory & Metadata (`module.json`)

Create a new folder inside `modules/` (e.g., `modules/my_module/`).
Inside this folder, create a `module.json` file. This tells AMIKO how to populate the Sidebar.

```json
{
    "name": "My New Module",
    "icon": "🚀",
    "enabled": true,
    "index": 50,
    "description": "A short sentence explaining exactly what the module does.",
    "author": "Your Name",
    "site": "https://link-to-your-site.com"
}
```
*Note: The folder name (`my_module`) will be automatically used as the module's unique internal ID.*

### Field Reference

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Display name shown in the sidebar. |
| `icon` | Yes | Emoji or inline SVG rendered next to the name. |
| `enabled` | Yes | Set to `false` to hide the module from the sidebar without deleting it. |
| `index` | Yes | Controls the display order in the sidebar. Lower values appear first (e.g., SSH Terminal uses `0`, Explorer uses `15`). |
| `description` | No | Short description for internal reference. |
| `author` | No | Module author name. |
| `site` | No | URL to the author or project page. |

---

## Step 2: Create the Visual Template (`template.html`)

Create `template.html` inside your module's folder. 
This HTML is automatically wrapped in a `<template>` tag by the server and cloned dynamically every time the user drags your module onto the grid.

⚠️ **GOLDEN RULE: DO NOT USE "IDs" (`id="..."`)**. 
Because the user can drag multiple instances of "My New Module" into the grid, using `id="myButton"` will cause DOM conflicts. 
**Always use classes** (e.g., `class="mod-my-button"`).

```html
<!-- Main Container (data-module-type must match your folder name) -->
<div class="amiko-module" data-module-type="my_module">
    
    <!-- Module Header -->
    <div class="module-header">
        <span class="module-title">🚀 My Module</span>
        <div class="module-actions">
            <button class="btn-expand" title="Expand/Collapse">⤢</button>
            <button class="btn-close" title="Close Module">❌</button>
        </div>
    </div>
    
    <!-- Module Body (Logic and UI) -->
    <div class="module-body">
        <button class="mod-btn-action">Click Me!</button>
        <div class="terminal-container">
            <div class="terminal-body large mod-terminal">
                <div class="log-entry log-dim">// Ready...</div>
            </div>
        </div>
    </div>

</div>
```

---

## Step 3: Ensure UI Consistency (CSS)

AMIKO's interface supports multiple themes via CSS custom properties defined in `static/css/themes/`. To seamlessly integrate your module, use the built-in global CSS variables and classes:

1. **Colors and Base Theme**:
   - Use CSS variables instead of HEX codes!
   - Main background: `var(--bg-primary)`
   - Primary text (bright green): `var(--text-primary)`
   - Secondary text (dim green): `var(--text-dim)`

2. **Buttons and Forms**:
   - Wrap inputs in `<div class="form-group">`.
   - Use `<button type="submit" class="mod-btn-submit">` for main glowing actions.

3. **"Hacker Terminal" Styling**:
   - If your module outputs logs, put them in `<div class="terminal-body">`.
   - Append rows using the `log-entry` class.
   - You can color logs using modifiers: `log-success` (light green), `log-error` (red), `log-info` (cyan), or `log-dim` (faded green). *(Tip: Use the global `addLogEntry` JS function)*.

---

## Step 4: Create the Frontend Logic (`client.js`)

Create `client.js` in your module's folder. This script is automatically injected into the browser by the AMIKO loader.

You must create a Class that accepts the **module's DOM container** in its constructor. This allows you to scope all your DOM queries to *your specific instance* using `this.container.querySelector(...)`.

```javascript
/*
 * AMIKO - My New Module Logic
 */
class MyModule {
    constructor(container) {
        // 'container' is the DOM instance of your module cloned from template.html
        this.container = container; 
        
        // 1. Find elements using querySelector
        this.btn = this.container.querySelector('.mod-btn-action');
        this.terminal = this.container.querySelector('.mod-terminal');
        
        // 2. Bind Events
        if (this.btn) {
            this.btn.addEventListener('click', this.doAction.bind(this));
        }

        // 3. Listen to Global Connection Events (Recommended)
        this.globalConnectHandler = this.onGlobalConnect.bind(this);
        this.globalDisconnectHandler = this.onGlobalDisconnect.bind(this);
        window.addEventListener('amikoGlobalConnect', this.globalConnectHandler);
        window.addEventListener('amikoGlobalDisconnect', this.globalDisconnectHandler);
        
        // Check if we are already connected upon instantiation
        if (window.globalConnection && window.globalConnection.connected) {
            this.onGlobalConnect();
        }
    }

    doAction() {
        if (!window.globalConnection || !window.globalConnection.connected) {
            alert('Please connect via the Global Connection first!');
            return;
        }

        // Access global credentials
        const host = window.globalConnection.hostname;
        
        // Write a log (using AMIKO's global addLogEntry utility)
        addLogEntry(this.terminal, `Action executed on ${host}...`, 'success');
    }

    onGlobalConnect() {
        addLogEntry(this.terminal, "Server connected! Features unlocked.", "info");
    }

    onGlobalDisconnect() {
        addLogEntry(this.terminal, "Server disconnected.", "error");
    }

    // Crucial Method: Called by AMIKO when the user closes the module (Red X button)
    destroy() {
        console.log("Module destroyed!");
        
        // Clean up listeners to prevent memory leaks!
        window.removeEventListener('amikoGlobalConnect', this.globalConnectHandler);
        window.removeEventListener('amikoGlobalDisconnect', this.globalDisconnectHandler);
        
        // Close event sources, intervals, or WebSockets here...
    }
}

// REGISTER THE MODULE WITH THE CORE SYSTEM
// The string 'my_module' MUST match your exact folder name!
AmikoModules.register('my_module', MyModule);
```

---

## Step 5: (Optional) Python Backend API (`backend.py`)

If your module requires custom server-side execution (like searching files or querying a database), create a `backend.py` file in your module folder. 

AMIKO will automatically detect it and try to register a Flask **Blueprint** named `blueprint`.

```python
from flask import Blueprint, request, jsonify

# The blueprint object MUST be named 'blueprint' so the loader can find it.
# The blueprint name (first argument) should ideally match your folder name.
blueprint = Blueprint('my_module', __name__)

@blueprint.route('/my-module-api/do-something')
def do_something():
    """ Custom API endpoint accessible at /my-module-api/do-something """
    host = request.args.get('hostname')
    return jsonify({"status": "success", "message": f"Processed {host} on backend."})
```

From your `client.js`, you can simply `fetch('/my-module-api/do-something?hostname=...')`.

---

## Global Utilities Reference

While inside your module's `client.js` class, you can safely use these globally exposed tools provided by AMIKO:

### Connection State

- `window.globalConnection.connected` — (boolean) `true` if the main SSH/SFTP session is active.
- `window.globalConnection.hostname` — Current server hostname.
- `window.globalConnection.port` — Current server port.
- `window.globalConnection.username` — Current SSH username.
- `window.globalConnection.password` — Current SSH password.

### Terminal Logging

- `addLogEntry(terminalElement, message, type)` — Appends a styled log line to a terminal container. Supported types: `'log'`, `'info'`, `'success'`, `'error'`, `'match'`, `'dim'`.
- `clearTerminal(terminalElement)` — Clears all log entries from the terminal container.
- `scrollTerminalToBottom(terminalElement)` — Scrolls the terminal to the latest entry.
- `formatTimestamp()` — Returns the current date/time as a localized string.

### Dialog Windows

AMIKO provides three global dialog functions that display styled WinBox popups instead of native browser dialogs. These are available from any module:

```javascript
// Error / Info / Success alert
window.AmikoAlert(title, message, type);
// type: 'error' (default), 'success', 'warning'

// Confirmation dialog with Yes/No buttons
window.AmikoConfirm(title, message, onConfirm);
// onConfirm is called (no arguments) if the user clicks Yes.

// Prompt dialog with text input
window.AmikoPrompt(title, message, defaultValue, onConfirm);
// onConfirm receives the entered string value.
```

Examples:

```javascript
// Show error
window.AmikoAlert('Error', 'Connection failed: timeout');

// Ask for confirmation before a destructive action
window.AmikoConfirm('Delete', 'Are you sure?', () => {
    // User clicked Yes
    this.performDelete();
});

// Ask user for a name
window.AmikoPrompt('Rename', 'Enter new name:', 'default.txt', (newName) => {
    this.renameFile(newName);
});
```

---

## Using WinBox Popups

AMIKO includes [WinBox.js](https://nextapps-de.github.io/winbox/) for creating draggable, resizable popup windows. This is useful for editors, image viewers, detail panels, or any content that should float above the grid.

To create a popup that matches the AMIKO visual style, use the `wb-amiko` class:

```javascript
const popup = new WinBox({
    title: 'My Popup',
    class: ['wb-amiko'],
    html: '<div style="padding: 20px;">Content here</div>',
    width: 500,
    height: 350,
    x: 'center',
    y: 'center',
    top: 70,        // Prevents overlap with the AMIKO header
    index: 9999,    // Ensures popup sits above grid modules
    onclose: () => {
        // Cleanup logic when the window is closed
    }
});
```

You can also mount a DOM element instead of raw HTML:

```javascript
const container = document.createElement('div');
container.innerHTML = '...';

new WinBox({
    title: 'Mounted Content',
    class: ['wb-amiko'],
    mount: container,
    // ... other options
});
```

---

## Real-Time Communication with SocketIO

For modules that require real-time, bidirectional communication (live data streams, interactive sessions, etc.), AMIKO provides a SocketIO server via `flask-socketio`.

The SocketIO client library is globally available as `io()`. Each module that needs its own WebSocket channel should create a dedicated connection with `forceNew: true` to avoid conflicts:

```javascript
// In your module's constructor
this.socket = io({ forceNew: true });

// Listen for events from the server
this.socket.on('my_module_output', (data) => {
    console.log('Received:', data);
});

// Send events to the server
this.socket.emit('my_module_action', {
    hostname: window.globalConnection.hostname,
    command: 'some_command'
});
```

On the backend side (`backend.py`), SocketIO events are registered in `app.py` since `flask-socketio` event handlers must be bound to the main `socketio` instance. If your module needs custom SocketIO events, they should be added to `app.py` following the existing pattern:

```python
@socketio.on('my_module_action')
def handle_my_action(data):
    # Process the request
    emit('my_module_output', {'result': '...'}, to=request.sid)
```

Always disconnect in your module's `destroy()` method to prevent resource leaks:

```javascript
destroy() {
    if (this.socket) {
        this.socket.disconnect();
        this.socket = null;
    }
}
```
