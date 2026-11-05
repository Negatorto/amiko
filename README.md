<p align="center">
  <img src="static/img/AMIKO.png?v=0.7.3" alt="AMIKO Logo" width="230">
</p>

# AMIKO v0.7.3


**Advanced Multi-function Interface Kit for Operations**

AMIKO is a modular, web-based dashboard for remote server management built around a **desktop-environment UI** inspired by Ubuntu/GNOME. Each tool runs as an independent floating window (powered by WinBox.js) opened from a vertical sidebar dock.

Built with Flask and WebSocket, it connects to remote servers via SSH/SFTP and presents all operations through a single, unified interface.

---

## What's New in v0.7.3

- **Vault Key Protection & OS Keyring Integration**:
  - **Decoupled Vault Security**: Sensitive configuration, server credentials, notes, and firewall rules encrypted with Fernet AES-128-CBC are no longer tied exclusively to the local disk key (`data/.amiko_key`).
  - **3 Protection Modes**:
    - **Local Key File (`local_file`)**: Classic unattended startup mode with `data/.amiko_key` guarded at `0600` permissions.
    - **OS Keyring (`os_keyring`)**: Securely stores the 256-bit Fernet Data Encryption Key (DEK) in the host's native credential store (Freedesktop Secret Service / `secret-tool`, Linux Kernel User Keyring `@u`, macOS Keychain, or `python-keyring`), immediately overwriting and wiping `data/.amiko_key` from disk blocks. Includes optional Recovery Passphrase for cold-boot fallback.
    - **Master Passphrase (`passphrase`)**: Wraps the DEK using PBKDF2-HMAC-SHA256 (600,000 iterations + 16-byte random salt) in `data/vault_meta.json`, wiping `data/.amiko_key` from disk.
  - **Runtime Seal / Unseal Flow**:
    - When protected by a Master Passphrase, AMIKO boots in a **SEALED** state (protecting server secrets in case of stolen disk images or VM snapshots).
    - Unseal the vault directly from the web login screen (`/login`) via dedicated Master Passphrase challenge or using the `AMIKO_MASTER_PASSPHRASE` / `AMIKO_VAULT_KEY` environment variables.
    - Admins can lock the vault into RAM-sealed mode at any moment with **Seal Vault Now**.
  - **Atomic Key Rotation (`Rotate DEK`)**:
    - Generate a fresh 256-bit Fernet key and atomically re-encrypt all encrypted at-rest stores (`settings.json.enc`, `notes_data.enc`, and `firewall.json.enc`) without service interruption.
  - **Dedicated Vault CLI (`utils/vault_ctl.py`)**:
    - Command-line tool for server administrators to inspect vault status, switch modes, rotate keys, and unseal without web access:
      ```bash
      python3 utils/vault_ctl.py --status           # Inspect vault state, key ID, and OS keyring backend
      python3 utils/vault_ctl.py --set-passphrase   # Wrap DEK with Master Passphrase and wipe .amiko_key
      python3 utils/vault_ctl.py --set-keyring      # Move DEK to OS Keyring and wipe .amiko_key
      python3 utils/vault_ctl.py --set-local-file   # Restore DEK to data/.amiko_key (unattended mode)
      python3 utils/vault_ctl.py --rotate-key       # Generate new DEK and atomically re-encrypt all stores
      ```

---

## What's New in v0.7.2

- **Per-Theme Canvas Background Engine & Aesthetics Overhaul**:
  - AMIKO now looks as good as it works! Introduced a high-performance 60 FPS HTML5 Canvas engine with 12 handcrafted, immersive visual themes:
    - **Cyberpunk**: Multi-layer Night City skyline with deep atmospheric sky gradients, volumetric light beams, glowing megatower window grids, and floating illuminated AVs.
    - **Batman**: Minimalist pitch-black stealth cowl silhouette with glowing white eyes that track mouse gaze and squint on click, accented with rare distant lightning flashes.
    - **Classic**: Launchpad matrix grid with warm amber/orange tiles and subtle, rare (~9%) Ottanio accents with smooth 4–5s organic breathing fades.
    - **Fallout Pip-Boy**: Amber-green phosphor terminal with `[ AMIKO-TEC OS V1.04 ]` telemetry and a clean, responsive reticle.
    - **Space Invaders**: Vintage 8-bit marching alien fleet canvas without cluttering HUD overlays.
    - **High Contrast**: Clean, distraction-free geometric backdrop with high-visibility vibrant orange accents.
    - **Nordic Slate**: Sober, undulating topological sine waves for focused, professional workspaces.
    - **Matrix, Synthwave, Barbie, Pacman, AMIKO Base**: Fine-tuned canvas animations with smooth transitions and zero layout disruption.
- **Declarative Module Permission Manifests & Integrity Verification**:
  - `module.json` declarative security manifests specify `allowed_roles`, granular `capabilities`, and `write_endpoints`.
  - Integrity verification engine (`utils/verify_modules.py`) with SHA-256 digests (`--sign`, `--verify`, `--status`) and strict enforcement preventing tampering.
  - Server-side RBAC enforcement rejecting unauthorized module actions, even if client requests are forged.
- **Modular Guidelines & Documentation**:
  - Full developer documentation for module authoring, manifest schemas, and permission declaration in `MODULE_GUIDELINES.md`.

---

## What's New in v0.7.1

- **New Module — Services & Containers (`services`)**:
  - **Active Services & Listening Ports**: real-time discovery of open TCP and UDP sockets correlated with systemd service units and standalone processes.
  - **1-Click Web Service Launcher**: automatic detection of web applications and control panels (HTTP/HTTPS) on standard and custom ports with direct `🌐 OPEN :PORT` browser buttons.
  - **Service Lifecycle Controls**: start, stop, restart, and inspect status and recent journal logs with an integrated inspector drawer. Includes anti-lockout safeguards protecting SSH and network services from accidental termination.
  - **Docker & Podman Containers**: auto-detects installed container runtimes, displays container health, port mappings with web launchers, and allows start, stop, restart, and log inspection.
- **Upgraded Module — Searcher (`searcher`)**:
  - Replaces and expands the former `keyword_search` module into a dual-mode search tool:
    - **File Search Tab**: recursive search for files and folders by name or wildcard pattern (e.g. `*.conf`, `backup*`), with target filters (files, directories, all) and case-sensitivity toggle.
    - **Keyword Search Tab**: full text content search across remote files with line numbers and match summaries.
  - Includes transparent backwards compatibility for existing saved layouts.

---

## What's New in v0.7.0 (Defense)

This release completed perimeter defence and incident detection capabilities:

- **L7 host firewall**: ordered INPUT/OUTPUT rules (source/destination CIDR, interface, protocol, port/service, state) with ▲/▼ reordering, a default policy per chain and egress control for outbound SSH/SFTP connections.
- **Ports & Services dictionary**: named services (`AMIKO-WEB`, `SSH`, `HTTP`, `HTTPS`, `PING`, or your own) that firewall rules can reference.
- **Fail2Ban multi-jail**: `amiko-login`, `amiko-2fa`, `amiko-sec`, `recidive`, plus a global whitelist, manual bans and a real-time banned-IP table with countdown.
- **Kernel sync (optional)**: bans are mirrored into nftables/iptables when AMIKO runs with `CAP_NET_ADMIN`.
- **Anti-lockout safeguard**: loopback is always accepted, and changes that would cut off your own connection are refused.
- **Delegated permission**: admins can grant `can_manage_firewall` to individual operators.
- **Break-glass CLI**: `utils/reset_firewall.py` (`--status`, `--unban`, `--disable`, `--flush-bans`, `--disable-fail2ban`), picked up by the running server and fully audited.
- **Security email notifications**: STARTTLS/SSL SMTP alerts to a security contact, with immediate critical alerts, anti-flood digests, non-blocking retries, deadline reminders and an hourly audit-chain integrity check.
- **Hardening**:
  - `X-Forwarded-For` is now trusted only from configured reverse proxies (`AMIKO_TRUSTED_PROXIES`).
  - The audit hash chain stays consistent when written by multiple processes.
- **Fix**: the SSH vitals stream (`/ssh-stats`) no longer fails with a missing import.

> **Upgrade note**: if AMIKO runs behind a reverse proxy on a different host, add the proxy address to `AMIKO_TRUSTED_PROXIES`. Otherwise every client will appear with the proxy's IP.

---

## Features

- **Modular architecture** — each tool is an isolated plugin with its own UI, logic, and optional backend routes. Modules are auto-discovered at startup.
- **Desktop-style UI** — a fixed top bar (clock, vitals, connection), a vertical sidebar dock, and floating WinBox windows that stay within the desktop area.
- **Role-Based Access Control (RBAC)** — multi-user authentication with three privilege levels (`admin`, `operator`, `viewer`) and per-group server isolation following the Principle of Least Privilege.
- **Firewall & Fail2Ban** — L7 host firewall with ordered rules (INPUT/OUTPUT), a ports & services dictionary, multi-jail Fail2Ban with global whitelist, optional nftables/iptables kernel sync and delegable `can_manage_firewall` permission.
- **Security email notifications** — critical security events, attacks and sensitive changes from the audit trail are emailed to a security contact over STARTTLS/SSL, with digests to prevent mail floods.
- **Global connection** — connect once, and all modules share the same SSH/SFTP session. No need to re-enter credentials per tool.
- **SSH connection pool** — the Explorer module reuses SSH transports across requests, eliminating per-request handshake overhead.
- **Persistent layouts** — save and restore open-module configurations across sessions.

## Included Modules

| Module | Description |
|--------|-------------|
| **Explorer** | Browse, read, and edit remote files via SFTP. Supports text editing with syntax-aware features and image preview. |
| **SSH Terminal** | Full interactive terminal session using xterm.js. |
| **Services** | Inspect active system services, listening ports with 1-click Web UI launcher, and manage Docker/Podman containers (start, stop, restart, logs). |
| **Searcher** | Dual-section search tool: classic file/folder search by name or wildcard pattern, plus content keyword search across remote directories. |
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

### Two-Factor Authentication (2FA / TOTP)

AMIKO includes per-user Two-Factor Authentication compatible with **Google Authenticator**, Authy, and any standard RFC 6238 TOTP app:

- **First Login & Pairing**: When a user signs in for the first time (or after an admin reset), AMIKO presents a setup screen with a pairing QR code, manual secret key, and 5 single-use emergency recovery codes. Entering the first 6-digit code confirms pairing and activates 2FA.
- **Subsequent Logins**: Users enter their username and password, followed by the dynamic 6-digit code from Google Authenticator (or an emergency recovery code).
- **Admin Management**: Administrators can view 2FA status for all accounts and reset 2FA registration for any user in **Settings → Users**.
- **CLI Emergency Rescue Tool**: If an administrator or user loses access to their authenticator device, server operators can reset 2FA directly from the server terminal:
  ```bash
  # Check 2FA status for all users
  python3 utils/reset_2fa.py --list

  # Reset 2FA for a specific user
  python3 utils/reset_2fa.py <username>
  ```
  After reset, the user will be prompted to scan a new QR code on their next login.

### Firewall & Fail2Ban (Settings → Firewall)

AMIKO protects its own management plane on two levels:

1. **Level 1 — Application layer (no root needed)**: a WSGI middleware checks every HTTP request and WebSocket handshake, before routing, against the ban list and the ordered firewall rules. Outbound SSH/SFTP connections to managed servers are checked against the `OUTPUT` rules (egress control).
2. **Level 2 — Kernel sync (optional, admin only)**: when AMIKO runs with `CAP_NET_ADMIN`, banned IPs are mirrored into nftables (`table inet amiko`) or iptables (`AMIKO-BANS` chain). Without the privilege, AMIKO keeps using Level 1 only.

- **Traffic Rules**: evaluated top-down, the first matching rule wins; unmatched traffic follows the chain's default policy. At L7, `ESTABLISHED` means a request with a valid authenticated session. UDP/ICMP rules are flagged `kernel-only` because that traffic never reaches AMIKO. The firewall is disabled by default.
- **Anti-lockout safeguard**: loopback is always accepted, and AMIKO refuses any change that would block your own connection (HTTP 409).
- **Fail2Ban jails**: `amiko-login` (password brute force), `amiko-2fa` (TOTP guessing), `amiko-sec` (unauthenticated probing of unknown paths, CSRF/CSWSH, attempts to reach reserved endpoints) and `recidive` (long ban for IPs banned repeatedly). Each jail has its own Max Retry, Find Time and Ban Time (`-1` = permanent). Fail2Ban is enabled by default.
- **Delegation**: admins can grant `can_manage_firewall` to individual operators in **Settings → Users**.
- **Reverse proxy**: `X-Forwarded-For` is trusted only from `AMIKO_TRUSTED_PROXIES` (comma-separated CIDRs, default `127.0.0.1/32,::1/128`; set `none` to disable). Trusted proxies are never banned.
- **Break-glass CLI** (applied to a running server within ~1 s, and audited):
  ```bash
  python3 utils/reset_firewall.py --status            # rules, jails, banned IPs
  python3 utils/reset_firewall.py --unban <ip>        # unban one IP
  python3 utils/reset_firewall.py --disable           # disable the rule engine
  python3 utils/reset_firewall.py --flush-bans        # remove all bans
  python3 utils/reset_firewall.py --disable-fail2ban  # stop automatic banning
  ```

### Security Email Notifications (Settings → Alerts, admin only)

AMIKO emails security events from the audit trail to a single security contact (e.g. a CISO or SOC mailbox). Emails are in English.

- **Critical** (sent immediately): SSH host key mismatch, tampered audit hash chain (checked every hour), recidive bans, any break-glass CLI action. These emails include a reminder of the NIS2 Art. 23 deadlines (24h / 72h / 1 month).
- **Attacks & blocks**: Fail2Ban bans, login lockouts, CSRF/CSWSH blocks, firewall lockout and egress blocks.
- **Sensitive changes**: users, 2FA resets and recovery code use, backup restore, firewall and Fail2Ban configuration.
- **Flood protection**: after the first email, more events of the same type are grouped into one digest per cooldown window (default 10 min).
- **Transport**: STARTTLS or SSL/TLS only, with certificate verification. The SMTP password is stored in the encrypted vault and never sent to the browser.
- **Reliability**: emails are sent in the background with non-blocking retries (5 s / 30 s / 2 min). Every outcome is audited (`NOTIFY_SENT` / `NOTIFY_FAILED`).
- **Anti-tampering**: any change to the notification settings is reported to the previous recipient.

### Encrypted Vault Key Protection & OS Keyring (Settings → Interface)

AMIKO encrypts all sensitive stores at rest (`data/settings.json.enc`, `data/notes_data.enc`, `data/firewall.json.enc`) using a 256-bit Fernet Data Encryption Key (DEK). Administrators can choose how that DEK is protected:

1. **Local Key File (`local_file`, default)**: The DEK is stored in `data/.amiko_key` (`0600` permissions) for unattended server startup.
2. **OS Keyring (`os_keyring`)**: The DEK is stored in the Operating System's native credential store (Freedesktop Secret Service / `secret-tool`, Linux Kernel User Keyring `@u`, macOS Keychain, or `python-keyring`) and `data/.amiko_key` is securely overwritten and wiped from disk. Supports an optional recovery passphrase for cold-boot fallback.
3. **Master Passphrase (`passphrase`)**: The DEK is wrapped with a key derived from the administrator's Master Passphrase via `PBKDF2-HMAC-SHA256` (600,000 iterations) in `data/vault_meta.json`, and `data/.amiko_key` is securely wiped from disk. On startup (unless `AMIKO_MASTER_PASSPHRASE` or `AMIKO_VAULT_KEY` is provided in the environment), AMIKO starts in a **SEALED** state and prompts to unseal the vault in RAM on the login screen.

- **Key Rotation & Manual Seal**: Administrators can rotate the DEK (atomically re-encrypting all vault stores) or manually seal the vault in RAM at any time from **Settings → Interface** or via the CLI tool:
  ```bash
  python3 utils/vault_ctl.py --status           # Inspect vault mode, key ID, and OS keyring backend
  python3 utils/vault_ctl.py --set-passphrase   # Switch to Master Passphrase mode and wipe .amiko_key
  python3 utils/vault_ctl.py --set-keyring      # Move key to OS Keyring and wipe .amiko_key
  python3 utils/vault_ctl.py --set-local-file   # Restore key to data/.amiko_key
  python3 utils/vault_ctl.py --rotate-key       # Rotate DEK and re-encrypt settings, notes, and firewall
  ```

## Project Structure

```
amiko/
├── app.py                  # Main server entry point
├── core/                   # Core application modules
│   ├── __init__.py         # App factory and SocketIO init
│   ├── audit.py            # Tamper-evident SHA-256 hash-chained audit logger & rate limiter
│   ├── auth.py             # Session authentication and 3-role RBAC engine
│   ├── client_ip.py        # Trusted client IP resolution (reverse proxy aware)
│   ├── config.py           # Globals and settings
│   ├── firewall.py         # L7 firewall, Fail2Ban jails, kernel sync (nftables/iptables)
│   ├── notifier.py         # Security email notifications (audit trail subscriber)
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
│   │   ├── firewall.js     # Firewall & Fail2Ban settings panel
│   │   ├── notifications.js # Email notifications settings panel
│   │   └── theme.js        # Theme switcher
│   └── lib/                # Third-party libraries (xterm, socket.io)
├── modules/                # Plugin modules (auto-discovered)
│   ├── explorer/
│   ├── ssh/
│   ├── services/
│   ├── searcher/
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

- `module.json` — metadata (name, icon, description), declarative `permissions` manifest, and SHA-256 `integrity` digests
- `template.html` — the UI fragment rendered inside a WinBox window
- `client.js` — frontend logic (class-based, scoped to the module container)
- `backend.py` *(optional)* — Flask Blueprint for server-side API routes

After creating or modifying a module, sign its integrity manifest:
```bash
python3 utils/verify_modules.py --sign
python3 utils/verify_modules.py --status
```

See [MODULE_GUIDELINES.md](MODULE_GUIDELINES.md) for the complete development guide.

## Status

AMIKO is in early development (v0.7). The core architecture is functional, but the API surface and module contracts may change as the project evolves.

## Roadmap to v1.0.0

### Core & Architecture
- [x] Refactor `app.py` into dedicated modules (routes, handlers, config)
- [x] Move SSH credentials from URL query parameters to request body
- [x] Migrate inline styles from `index.html` to CSS
- [x] Standardize all modules icons to SVG format
- [x] Add favicon
- [x] Self-host all third-party frontend libraries (`static/lib/`) to remove external CDN dependencies
- [x] Module permission manifests (`module.json`) and plugin integrity verification

### Security & Access Control
- [x] Login screen with session authentication
- [x] Role-based access control (`admin`, `operator`, `viewer`) with per-group Least Privilege
- [x] Zero-password frontend (resolve server credentials strictly on the backend via opaque server IDs)
- [x] Login rate-limiting & progressive brute-force lockout protection
- [x] Asymmetric SSH key authentication (`Ed25519` / `RSA`) and strict `known_hosts` host-key verification
- [x] Deprecate plain-text FTP in favor of encrypted SFTP-only transport
- [x] Production transport hardening (Same-Origin CSRF/CSWSH protection, Content Security Policy & security headers, idle session timeout, encrypted Notes at rest)
- [x] Master Passphrase / OS Keyring integration for the encrypted vault (decoupling the encryption key from local disk)
- [x] Multi-Factor Authentication (TOTP / 2FA with Google Authenticator, Emergency Recovery Codes, and Admin/CLI reset)
- [x] Firewall & Fail2ban integration (L7 rule engine, multi-jail Fail2Ban, whitelist, kernel sync, delegated permission, break-glass CLI)
- [x] Ports & Services dictionary and egress (OUTPUT) control for outbound SSH/SFTP connections
- [x] Trusted reverse-proxy client IP resolution (`AMIKO_TRUSTED_PROXIES`) to prevent `X-Forwarded-For` spoofing

### Auditing, Operations & Resilience
- [x] Structured immutable audit logging (SHA-256 hash chain) for authentication, configuration changes, and remote operations
- [x] On-demand snapshot backup & restore (with optional PBKDF2-SHA256 + AES encryption)
- [ ] Interactive SSH terminal session recording and forensic playback
- [x] SMTP server configuration for security alerts and automated notification emails (critical/attack/change categories, digests, audited delivery)
- [x] Periodic audit hash-chain integrity verification with critical alerting

### UI/UX
- [x] Desktop-environment UI (top bar, sidebar dock, floating WinBox windows)
- [x] GNOME style CPU/RAM/DISK monitor in top bar
- [x] Live clock in top bar
- [x] Connection panel as topbar popover + active VM name & IP indicator in bottom bar
- [x] Material Design vector SVG icon system across Settings, Groups, Servers, and Explorer
- [x] Settings panel improvements (export/import config, multi-column group cards, horizontal server form)

### Performance
- [x] Persistent SSH & SFTP subsystem channel pooling in Explorer (eliminates per-request handshake and subsystem spawn overhead)
- [x] Stale-While-Revalidate in-memory directory cache, SFTP file prefetching, and batched `DocumentFragment` DOM rendering
- [x] Single-channel combined `/proc/stat` telemetry for the topbar vitals monitor
- [x] Debounced line-number rendering in the file editor
- [x] AbortController for Explorer directory navigation

## License

This project is licensed under the [GNU General Public License v3.0](LICENSE).
You are free to use, modify, and redistribute this software under the terms of the GPL v3.
