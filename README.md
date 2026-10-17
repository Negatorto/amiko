<p align="center">
  <img src="static/img/AMIKO.png" alt="AMIKO Logo" width="230">
</p>

# AMIKO


**Advanced Multi-function Interface Kit for Operations**

AMIKO is a modular, web-based dashboard for remote server management built around a **desktop-environment UI** inspired by Ubuntu/GNOME. Each tool runs as an independent floating window (powered by WinBox.js) opened from a vertical sidebar dock.

Built with Flask and WebSocket, it connects to remote servers via SSH/SFTP and presents all operations through a single, unified interface.

---

## Features

- **Modular architecture** — each tool is an isolated plugin with its own UI, logic, and optional backend routes. Modules are auto-discovered at startup.
- **Desktop-style UI** — a fixed top bar (clock, vitals, connection), a vertical sidebar dock, and floating WinBox windows that stay within the desktop area.
- **Role-Based Access Control (RBAC)** — multi-user authentication with three privilege levels (`admin`, `operator`, `viewer`) and per-group server isolation following the Principle of Least Privilege.
- **Global connection** — connect once, and all modules share the same SSH/SFTP session. No need to re-enter credentials per tool.
- **SSH connection pool** — the Explorer module reuses SSH transports across requests, eliminating per-request handshake overhead.
- **Persistent layouts** — save and restore open-module configurations across sessions.

## Included Modules

| Module | Description |
|--------|-------------|
| **Explorer** | Browse, read, and edit remote files via SFTP. Supports text editing with syntax-aware features and image preview. |
| **SSH Terminal** | Full interactive terminal session using xterm.js. |
| **Keyword Search** | Search for text patterns across remote directories. |
| **Large Files** | Identify and list the largest files on a remote filesystem. |
| **Notes App** | Per-server scratchpad for saving quick notes and annotations. |

## Installation & Setup

### 1. Prerequisites
- Python 3.10+
- git

### 2. Get the code
```bash
git clone https://github.com/Negatorto/amiko.git
cd amiko
```

### 3. Create a Virtual Environment (Recommended)
Using a virtual environment ensures that the project dependencies are isolated from your system.

**On Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
```

**On Windows:**
```powershell
python -m venv venv
.\venv\Scripts\activate
```

### 4. Install Dependencies
```bash
pip install -r requirements.txt
```

### 5. Run AMIKO
```bash
python3 app.py
```
The server will start at `http://localhost:5423`.

> **Default Login**: On first startup, AMIKO creates a default administrator account (`admin` / `admin`). You can change the password and manage `admin`, `operator`, and `viewer` accounts in **Settings → Users**.

## Project Structure

```
amiko/
├── app.py                  # Main server entry point
├── core/                   # Core application modules
│   ├── __init__.py         # App factory and SocketIO init
│   ├── auth.py             # Session authentication and 3-role RBAC engine
│   ├── config.py           # Globals and settings
│   ├── plugins.py          # Module auto-discovery logic
│   ├── routes.py           # HTTP endpoints
│   └── handlers.py         # WebSocket events
├── requirements.txt
├── templates/
│   ├── index.html          # Application shell
│   └── login.html          # Authentication screen
├── static/
│   ├── css/
│   │   ├── main.css        # Core styles + top/bottom bar + WinBox theme
│   │   ├── modules.css     # Sidebar dock + desktop area
│   │   ├── connection.css  # Connection popover
│   │   └── themes/         # Theme CSS files
│   ├── js/
│   │   ├── app.js          # Global dialogs (AmikoAlert / AmikoConfirm / AmikoPrompt)
│   │   ├── drag-drop.js    # Desktop launcher (WinBox) + module registry
│   │   ├── connection.js   # SSH connection management
│   │   ├── settings.js     # Settings overlay
│   │   └── theme.js        # Theme switcher
│   └── lib/                # Third-party libraries (xterm, socket.io)
├── modules/                # Plugin modules (auto-discovered)
│   ├── explorer/
│   ├── ssh/
│   ├── keyword_search/
│   ├── large_files/
│   └── notes/
├── data/                   # Persistent data (encrypted settings vault, notes)
├── utils/                  # Shared utilities
└── MODULE_GUIDELINES.md    # Guide for developing custom modules
```

## Customization

### Themes

AMIKO ships more themes, ranging from dark terminals to light palettes. Each theme is a standalone CSS file in `static/css/themes/` that overrides a set of CSS custom properties (colors, borders, glows). You can create a new theme by duplicating any existing file and adjusting the variables.

### Custom Modules

New modules can be added without modifying the core application. Each module lives in its own folder under `modules/` and consists of:

- `module.json` — metadata (name, icon, description)
- `template.html` — the UI fragment rendered inside a WinBox window
- `client.js` — frontend logic (class-based, scoped to the module container)
- `backend.py` *(optional)* — Flask Blueprint for server-side API routes

See [MODULE_GUIDELINES.md](MODULE_GUIDELINES.md) for the complete development guide.

## Status

AMIKO is in early development (v0.4). The core architecture is functional, but the API surface and module contracts may change as the project evolves.

## Roadmap to v1.0.0

### Core & Architecture
- [x] Refactor `app.py` into dedicated modules (routes, handlers, config)
- [x] Move SSH credentials from URL query parameters to request body
- [x] Migrate inline styles from `index.html` to CSS
- [x] Standardize all modules icons to SVG format
- [x] Add favicon
- [ ] Self-host all third-party frontend libraries (`static/lib/`) to remove external CDN dependencies
- [ ] Module permission manifests (`module.json`) and plugin integrity verification

### Security & Access Control
- [x] Login screen with session authentication
- [x] Role-based access control (`admin`, `operator`, `viewer`) with per-group Least Privilege
- [ ] Multi-Factor Authentication (TOTP / 2FA) and login rate-limiting / brute-force protection
- [ ] Zero-password frontend (resolve server credentials strictly on the backend via opaque server IDs)
- [ ] Asymmetric SSH key authentication (`Ed25519` / `RSA`) and strict `known_hosts` host-key verification
- [ ] Master Passphrase / OS Keyring integration for the encrypted vault (decoupling the encryption key from local disk)
- [ ] Deprecate plain-text FTP in favor of encrypted SFTP-only transport
- [ ] Production transport hardening (strict CORS, CSRF tokens, Content Security Policy headers, local binding behind TLS reverse proxy)

### Auditing, Operations & Resilience
- [ ] Structured immutable audit logging for authentication, configuration changes, and remote operations
- [ ] Interactive SSH terminal session recording and forensic playback
- [ ] Automated encrypted backups and disaster recovery tooling

### UI/UX
- [x] Desktop-environment UI (top bar, sidebar dock, floating WinBox windows)
- [x] GNOME style CPU/RAM/DISK monitor in top bar
- [x] Live clock in top bar
- [x] Connection panel as topbar popover
- [ ] Settings panel improvements (export/import config, server management)
- [ ] Enhanced text editor in Explorer (syntax highlighting, search/replace)

### Performance
- [x] SSH connection pool in Explorer (eliminates per-request handshake)
- [x] Debounced line-number rendering in the file editor
- [x] AbortController for Explorer directory navigation

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).
You are free to use, modify, and redistribute this software under the terms of the GPL v3.
