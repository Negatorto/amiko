/*
 * AMIKO - Files Module
 * Large files search via SFTP
 */

class FilesModule {
    constructor(container) {
        this.container = container;
        this.form = container.querySelector('.amiko-mod-form');
        this.terminal = container.querySelector('.mod-terminal');
        this.btn = container.querySelector('.mod-btn-submit');

        this.pathInput = container.querySelector('.mod-files-path');
        this.thresholdInput = container.querySelector('.mod-files-threshold');
        this.thresholdDisplay = container.querySelector('.mod-threshold-display');

        this.filesEventSource = null;
        this.filesCheckedCount = 0;
        this.largeFilesCount = 0;

        this.globalConnectHandler = this.handleGlobalConnect.bind(this);
        this.globalDisconnectHandler = this.destroy.bind(this);

        window.addEventListener('amikoGlobalConnect', this.globalConnectHandler);
        window.addEventListener('amikoGlobalDisconnect', this.globalDisconnectHandler);

        if (this.form) {
            this.form.addEventListener('submit', this.startLargeFilesSearch.bind(this));
        }

        if (this.thresholdInput && this.thresholdDisplay) {
            this.thresholdInput.addEventListener('input', () => {
                this.thresholdDisplay.textContent = `${this.thresholdInput.value} MB`;
            });
            this.thresholdDisplay.textContent = `${this.thresholdInput.value} MB`;
        }
    }

    startLargeFilesSearch(event) {
        event.preventDefault();

        if (!window.globalConnection || !window.globalConnection.connected) {
            alert('You must connect first using the global form above!');
            return;
        }

        this.filesCheckedCount = 0;
        this.largeFilesCount = 0;

        const params = new URLSearchParams();
        params.append('session_id', window.globalConnection.sessionId);

        params.append('path', this.pathInput.value);
        params.append('threshold_mb', this.thresholdInput.value);

        if (this.filesEventSource) {
            this.filesEventSource.close();
        }

        clearTerminal(this.terminal);
        addLogEntry(this.terminal, `Initializing file search > ${this.thresholdInput.value} MB...`, 'info');

        this.btn.disabled = true;
        this.btn.textContent = 'SEARCHING...';

        this.filesEventSource = new EventSource('/search-large-files?' + params.toString());

        this.filesEventSource.onmessage = (e) => {
            const data = JSON.parse(e.data);
            let logType = 'log';

            if (data.type === 'info') logType = 'info';
            else if (data.type === 'success') logType = 'success';
            else if (data.type === 'match') {
                logType = 'match';
                this.largeFilesCount++;
            }
            else if (data.type === 'error') logType = 'error';
            else if (data.type === 'log') {
                logType = 'dim';
                this.filesCheckedCount++;
            }
            else if (data.type === 'done') {
                this.btn.disabled = false;
                this.btn.textContent = 'SCAN LARGE FILES';
                this.filesEventSource.close();
                this.showFilesSummary();
                return;
            }

            addLogEntry(this.terminal, data.message, logType);
        };

        this.filesEventSource.onerror = (e) => {
            addLogEntry(this.terminal, 'Connection error or connection lost.', 'error');
            this.btn.disabled = false;
            this.btn.textContent = 'SCAN LARGE FILES';
            this.filesEventSource.close();
        };
    }

    showFilesSummary() {
        addLogEntry(this.terminal, `=== SUMMARY ===`, 'success');
        addLogEntry(this.terminal, `Files checked: ${this.filesCheckedCount}`, 'info');
        addLogEntry(this.terminal, `Large files found: ${this.largeFilesCount}`, 'match');
        addLogEntry(this.terminal, `===============`, 'success');
    }

    handleGlobalConnect() {
        // Optionally log or enable UI if we disabled it.
    }

    destroy() {
        if (this.filesEventSource) {
            this.filesEventSource.close();
        }
        window.removeEventListener('amikoGlobalConnect', this.globalConnectHandler);
        window.removeEventListener('amikoGlobalDisconnect', this.globalDisconnectHandler);
    }
}

// Register Module
AmikoModules.register('large_files', FilesModule);
