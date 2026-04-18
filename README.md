<p align="center">
  <img src="static/img/AMIKO.png" alt="AMIKO Logo" width="200">
</p>

# AMIKO

**Advanced Multi-function Interface Kit for Operations**

AMIKO is a modular, web-based dashboard for remote server management. It provides a drag-and-drop workspace where each tool runs as an independent, self-contained module.

Built with Flask and WebSocket, it connects to remote servers via SSH/SFTP and presents all operations through a single, unified interface.

---

## Features

- **Modular architecture** — each tool is an isolated plugin with its own UI, logic, and optional backend routes. Modules are auto-discovered at startup.
- **Drag-and-drop workspace** — arrange modules freely on a grid layout powered by GridStack.js. Save, load, and switch between workspace configurations.
- **Global connection** — connect once, and all modules share the same SSH/SFTP session. No need to re-enter credentials per tool.
- **Persistent settings** — server profiles, workspace layouts, and preferences are saved across sessions.

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

## Project Structure

```
amiko/
├── app.py                  # Main server entry point
├── core/                   # Core application modules
│   ├── __init__.py         # App factory and SocketIO init
│   ├── config.py           # Globals and settings
│   ├── plugins.py          # Module auto-discovery logic
│   ├── routes.py           # HTTP endpoints
│   └── handlers.py         # WebSocket events
├── requirements.txt
├── templates/
│   └── index.html          # Application shell
├── static/
│   ├── css/
│   │   ├── main.css        # Core styles
│   │   └── themes/         # Theme CSS files
│   ├── js/                 # Application scripts
│   └── lib/                # Third-party libraries (xterm, socket.io)
├── modules/                # Plugin modules (auto-discovered)
│   ├── explorer/
│   ├── ssh/
│   ├── keyword_search/
│   ├── large_files/
│   └── notes/
├── data/                   # Persistent data (servers, layouts, notes)
├── utils/                  # Shared utilities
└── MODULE_GUIDELINES.md    # Guide for developing custom modules
```

## Customization

### Themes

AMIKO ships more themes, ranging from dark terminals to light palettes. Each theme is a standalone CSS file in `static/css/themes/` that overrides a set of CSS custom properties (colors, borders, glows). You can create a new theme by duplicating any existing file and adjusting the variables.

### Custom Modules

New modules can be added without modifying the core application. Each module lives in its own folder under `modules/` and consists of:

- `module.json` — metadata (name, icon, description)
- `template.html` — the UI fragment rendered inside the grid
- `client.js` — frontend logic
- `backend.py` *(optional)* — Flask Blueprint for server-side API routes

See [MODULE_GUIDELINES.md](MODULE_GUIDELINES.md) for the complete development guide.

## Status

AMIKO is in early development (v0.3). The core architecture is functional, but the API surface and module contracts may change as the project evolves.

## Roadmap to v1.0.0

### Core
- [x] Refactor `app.py` into dedicated modules (routes, handlers, config)
- [x] Move SSH credentials from URL query parameters to request body
- [ ] Standardize error handling across all modules
- [x] Migrate inline styles from `index.html` to CSS
- [x] Standardize all modules icons to SVG format
- [ ] Add favicon

### Security
- [ ] Login screen with user authentication
- [ ] Role-based access control (admin, operator, viewer)

### UI/UX
- [ ] Settings panel improvements (export/import config, server management)
- [ ] Enhanced text editor in Explorer (syntax highlighting, line numbers, search)

### New Modules
- [ ] Service Manager — manage systemd services and Docker containers on remote servers

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).
You are free to use, modify, and redistribute this software under the terms of the GPL v3.
