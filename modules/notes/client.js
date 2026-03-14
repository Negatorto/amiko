/*
 * AMIKO - Notes Module
 * Per-server session notes with auto-save
 */

class NotesModule {
    constructor(container) {
        this.container = container;
        this.textarea = container.querySelector('.mod-notes-textarea');
        this.statusBar = container.querySelector('.mod-notes-status');
        this.hostLabel = container.querySelector('.mod-notes-host');

        this.currentHostname = null;
        this.saveTimeout = null;

        // Bind event handlers for cleanup
        this.globalConnectHandler = this.onGlobalConnect.bind(this);
        this.globalDisconnectHandler = this.onGlobalDisconnect.bind(this);

        window.addEventListener('amikoGlobalConnect', this.globalConnectHandler);
        window.addEventListener('amikoGlobalDisconnect', this.globalDisconnectHandler);

        if (this.textarea) {
            this.textarea.addEventListener('input', this.handleInput.bind(this));
            this.textarea.disabled = true;
        }

        // Check if already connected
        if (window.globalConnection && window.globalConnection.connected) {
            this.onGlobalConnect();
        }
    }

    async onGlobalConnect() {
        this.currentHostname = window.globalConnection.hostname;

        if (this.hostLabel) {
            this.hostLabel.textContent = this.currentHostname;
        }

        if (this.textarea) {
            this.textarea.disabled = false;
            this.textarea.placeholder = `// Notes for ${this.currentHostname}...`;
        }

        await this.loadNotes();
    }

    onGlobalDisconnect() {
        // Save current notes before disconnecting
        if (this.currentHostname && this.textarea) {
            this.saveNotes();
        }

        this.currentHostname = null;

        if (this.textarea) {
            this.textarea.value = '';
            this.textarea.disabled = true;
            this.textarea.placeholder = '// Connect to a server to use notes...';
        }
        if (this.hostLabel) {
            this.hostLabel.textContent = 'Not connected';
        }
        this.setStatus('idle');
    }

    handleInput() {
        if (!this.currentHostname) return;

        this.setStatus('typing');

        // Debounce: save 800ms after last keystroke
        if (this.saveTimeout) {
            clearTimeout(this.saveTimeout);
        }
        this.saveTimeout = setTimeout(() => {
            this.saveNotes();
        }, 800);
    }

    async loadNotes() {
        if (!this.currentHostname) return;

        this.setStatus('loading');

        try {
            const response = await fetch(`/notes/load?hostname=${encodeURIComponent(this.currentHostname)}`);
            const data = await response.json();

            if (this.textarea) {
                this.textarea.value = data.content || '';
            }
            this.setStatus(data.content ? 'saved' : 'idle');
        } catch (err) {
            console.error('[Notes] Load error:', err);
            this.setStatus('error');
        }
    }

    async saveNotes() {
        if (!this.currentHostname) return;

        this.setStatus('saving');

        try {
            await fetch('/notes/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    hostname: this.currentHostname,
                    content: this.textarea ? this.textarea.value : ''
                })
            });
            this.setStatus('saved');
        } catch (err) {
            console.error('[Notes] Save error:', err);
            this.setStatus('error');
        }
    }

    setStatus(state) {
        if (!this.statusBar) return;

        const states = {
            idle: '⏳ Ready',
            loading: '⏳ Loading...',
            typing: '✏️ Editing...',
            saving: '💾 Saving...',
            saved: '✅ Saved',
            error: '❌ Error'
        };

        this.statusBar.textContent = states[state] || '';
    }

    destroy() {
        // Save before closing
        if (this.currentHostname && this.textarea && this.textarea.value) {
            // Fire-and-forget save
            fetch('/notes/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    hostname: this.currentHostname,
                    content: this.textarea.value
                })
            }).catch(() => { });
        }

        if (this.saveTimeout) {
            clearTimeout(this.saveTimeout);
        }

        window.removeEventListener('amikoGlobalConnect', this.globalConnectHandler);
        window.removeEventListener('amikoGlobalDisconnect', this.globalDisconnectHandler);
    }
}

// Register Module
AmikoModules.register('notes', NotesModule);
