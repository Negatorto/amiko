/*
 * AMIKO - SSH Module
 * Real-time Xterm.js implementation with Socket.io
 */

class SSHModule {
    constructor(container) {
        this.container = container;
        this.terminalElement = container.querySelector('.amiko-term');

        this.term = null;
        this.socket = null;
        this.fitAddon = null;

        this.fitAddon = null;

        this.resizeHandler = this.fitTerminal.bind(this);
        this.globalConnectHandler = this.handleGlobalConnect.bind(this);
        this.globalDisconnectHandler = this.destroy.bind(this);

        // Listen for global connection changes
        window.addEventListener('amikoGlobalConnect', this.globalConnectHandler);
        window.addEventListener('amikoGlobalDisconnect', this.globalDisconnectHandler);

        // Wait a small delay to ensure DOM is fully rendered before mounting xterm
        setTimeout(() => {
            this.initSSHTerminal();
        }, 100);
    }

    initSSHTerminal() {
        console.log('Initializing Xterm.js terminal instance...');

        // 1. Initialize Terminal
        this.term = new Terminal({
            cursorBlink: true,
            theme: {
                background: window.AMIKO_THEME.bg_primary,
                foreground: window.AMIKO_THEME.text_primary,
                cursor: window.AMIKO_THEME.text_primary,
                selection: 'rgba(0, 255, 0, 0.3)'
            },
            fontFamily: "'Courier New', Courier, monospace",
            fontSize: 14,
            allowProposedApi: true
        });

        // 2. Load Fit Addon
        this.fitAddon = new FitAddon.FitAddon();
        this.term.loadAddon(this.fitAddon);

        // 3. Open Terminal in DOM
        this.terminalElement.innerHTML = '';
        this.term.open(this.terminalElement);
        this.fitAddon.fit();

        // 4. Connect Socket.io
        // WARNING: Using a global socket connection per module might cause resource issues or 
        // conflicts depending on the backend implementation of flask-socketio (request.sid). 
        // It's preferable if we had a namespace or module_id, but we'll use a new socket instance for now.
        this.socket = io({ forceNew: true });

        // Add click listener from anywhere in the terminal container to focus xterm
        const containerToFocus = this.terminalElement.parentElement || this.terminalElement;
        containerToFocus.addEventListener('click', () => {
            if (this.term) {
                this.term.focus();
            }
        });

        // 5. Setup Events
        this.socket.on('connect', () => {
            console.log('Socket.io connected for SSH Instance');
            this.term.write('\r\n*** WebSocket Connection Established ***\r\n');

            // Explicitly check current state
            if (window.globalConnection && window.globalConnection.connected) {
                this.startSSH();
            } else {
                this.term.write('\r\n*** Waiting for Global Connection... ***\r\n');
            }
        });

        this.socket.on('ssh_status', (data) => {
            if (data.status === 'connected') {
                this.term.write(`\r\n*** ${data.message} ***\r\n`);
                this.term.focus();
            } else if (data.status === 'error') {
                this.term.write(`\r\n*** SSH Error: ${data.message} ***\r\n`);
            }
        });

        this.socket.on('ssh_output', (data) => {
            this.term.write(data.data);
        });

        // Handle Input
        this.term.onData(data => {
            this.socket.emit('ssh_input', { data: data });
        });

        // Handle Resize using ResizeObserver
        this.resizeObserver = new ResizeObserver(() => {
            if (this.fitAddon && this.term && this.term.element) {
                // Check if element has actual layout (width > 0)
                if (this.terminalElement.clientWidth > 0 && this.terminalElement.clientHeight > 0) {
                    this.fitTerminal();
                }
            }
        });
        this.resizeObserver.observe(this.terminalElement);

    }

    startSSH() {
        if (!this.socket || !this.socket.connected) return;

        this.term.write('\r\n*** Authenticating... ***\r\n');
        this.socket.emit('connect_ssh', {
            hostname: window.globalConnection.hostname,
            port: window.globalConnection.port || 22,
            username: window.globalConnection.username,
            password: window.globalConnection.password
        });
    }

    handleGlobalConnect() {
        if (this.socket && this.socket.connected) {
            this.startSSH();
        }
    }

    fitTerminal() {
        if (this.fitAddon && this.term) {
            try {
                this.fitAddon.fit();
                // Send new dimensions to backend
                const dims = this.fitAddon.proposeDimensions();
                if (dims && this.socket) {
                    this.socket.emit('ssh_resize', { cols: dims.cols, rows: dims.rows });
                }
            } catch (e) {
                console.error("Fit terminal error", e);
            }
        }
    }

    destroy() {
        if (this.socket) {
            this.socket.disconnect();
            this.socket = null;
        }
        if (this.resizeObserver) {
            this.resizeObserver.disconnect();
            this.resizeObserver = null;
        }
        if (this.term) {
            this.term.dispose();
            this.term = null;
        }
        this.fitAddon = null;
        window.removeEventListener('amikoGlobalConnect', this.globalConnectHandler);
        window.removeEventListener('amikoGlobalDisconnect', this.globalDisconnectHandler);
        console.log('SSH Terminal Instance disconnected');
    }
}

// Register Module
AmikoModules.register('ssh', SSHModule);
