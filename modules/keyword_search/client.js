/*
 * AMIKO - Search Module
 * Keyword search in files via SFTP
 */

class SearchModule {
    constructor(container) {
        this.container = container;
        this.form = container.querySelector('.amiko-mod-form');
        this.terminal = container.querySelector('.mod-terminal');
        this.btn = container.querySelector('.mod-btn-submit');

        this.pathInput = container.querySelector('.mod-path');
        this.keywordInput = container.querySelector('.mod-keyword');
        this.caseSensitiveInput = container.querySelector('.mod-case-sensitive');

        this.searchEventSource = null;
        this.searchFilesChecked = 0;
        this.searchMatchesCount = 0;

        this.globalConnectHandler = this.handleGlobalConnect.bind(this);
        this.globalDisconnectHandler = this.destroy.bind(this);

        window.addEventListener('amikoGlobalConnect', this.globalConnectHandler);
        window.addEventListener('amikoGlobalDisconnect', this.globalDisconnectHandler);

        if (this.form) {
            this.form.addEventListener('submit', this.startKeywordSearch.bind(this));
        }
    }

    startKeywordSearch(event) {
        event.preventDefault();

        if (!window.globalConnection || !window.globalConnection.connected) {
            alert('You must connect first using the global form above!');
            return;
        }

        this.searchFilesChecked = 0;
        this.searchMatchesCount = 0;

        const params = new URLSearchParams();
        params.append('session_id', window.globalConnection.sessionId);

        params.append('path', this.pathInput.value);
        params.append('keyword', this.keywordInput.value);
        params.append('case_sensitive', this.caseSensitiveInput.value);

        if (this.searchEventSource) {
            this.searchEventSource.close();
        }

        clearTerminal(this.terminal);
        addLogEntry(this.terminal, 'Initializing search protocol...', 'info');

        this.btn.disabled = true;
        this.btn.textContent = 'SEARCHING...';

        this.searchEventSource = new EventSource('/search?' + params.toString());

        this.searchEventSource.onmessage = (e) => {
            const data = JSON.parse(e.data);
            let logType = 'log';

            if (data.type === 'info') logType = 'info';
            else if (data.type === 'success') logType = 'success';
            else if (data.type === 'match') {
                logType = 'match';
                this.searchMatchesCount++;
            }
            else if (data.type === 'error') logType = 'error';
            else if (data.type === 'log') {
                logType = 'dim';
                this.searchFilesChecked++;
            }
            else if (data.type === 'done') {
                this.btn.disabled = false;
                this.btn.textContent = 'START SEARCH';
                this.searchEventSource.close();
                this.showSearchSummary();
                return;
            }

            addLogEntry(this.terminal, data.message, logType);
        };

        this.searchEventSource.onerror = (e) => {
            addLogEntry(this.terminal, 'Connection error or connection lost.', 'error');
            this.btn.disabled = false;
            this.btn.textContent = 'START SEARCH';
            this.searchEventSource.close();
        };
    }

    showSearchSummary() {
        addLogEntry(this.terminal, `=== SUMMARY ===`, 'success');
        addLogEntry(this.terminal, `Files checked: ${this.searchFilesChecked}`, 'info');
        addLogEntry(this.terminal, `Matches found: ${this.searchMatchesCount}`, 'match');
        addLogEntry(this.terminal, `===============`, 'success');
    }

    handleGlobalConnect() {
        // Optionally log or enable UI if we disabled it.
    }

    destroy() {
        if (this.searchEventSource) {
            this.searchEventSource.close();
        }
        window.removeEventListener('amikoGlobalConnect', this.globalConnectHandler);
        window.removeEventListener('amikoGlobalDisconnect', this.globalDisconnectHandler);
    }
}

// Register Module
AmikoModules.register('keyword_search', SearchModule);
