/*
 * AMIKO - Searcher Module
 * Dual-section search tool:
 *   1. File Search (classic filename & wildcard pattern search)
 *   2. Keyword Search (content search inside remote files)
 */

class SearcherModule {
    constructor(container) {
        this.container = container;

        // Tab switcher elements
        this.tabButtons = container.querySelectorAll('.mod-searcher-tab-btn');
        this.panels = container.querySelectorAll('.mod-searcher-panel');

        // Section 1: File Search DOM references
        this.fileForm = container.querySelector('.amiko-mod-file-form');
        this.fileTerminal = container.querySelector('.mod-file-terminal');
        this.fileBtn = container.querySelector('.mod-btn-file-submit');
        this.filePathInput = container.querySelector('.mod-file-path');
        this.fileQueryInput = container.querySelector('.mod-file-query');
        this.fileTargetInput = container.querySelector('.mod-file-target');
        this.fileCaseInput = container.querySelector('.mod-file-case');

        // Section 2: Keyword Search DOM references
        this.keywordForm = container.querySelector('.amiko-mod-form');
        this.keywordTerminal = container.querySelector('.mod-terminal');
        this.keywordBtn = container.querySelector('.mod-btn-keyword-submit');
        this.pathInput = container.querySelector('.mod-path');
        this.keywordInput = container.querySelector('.mod-keyword');
        this.caseSensitiveInput = container.querySelector('.mod-case-sensitive');

        // SSE streams and counters
        this.fileEventSource = null;
        this.fileItemsChecked = 0;
        this.fileMatchesCount = 0;

        this.searchEventSource = null;
        this.searchFilesChecked = 0;
        this.searchMatchesCount = 0;

        // Global connection handlers
        this.globalConnectHandler = this.handleGlobalConnect.bind(this);
        this.globalDisconnectHandler = this.destroy.bind(this);

        window.addEventListener('amikoGlobalConnect', this.globalConnectHandler);
        window.addEventListener('amikoGlobalDisconnect', this.globalDisconnectHandler);

        // Bind tab switching
        this.tabButtons.forEach((btn) => {
            btn.addEventListener('click', () => {
                const targetTab = btn.getAttribute('data-searcher-tab');
                this.switchTab(targetTab);
            });
        });

        // Bind forms
        if (this.fileForm) {
            this.fileForm.addEventListener('submit', this.startFileSearch.bind(this));
        }

        if (this.keywordForm) {
            this.keywordForm.addEventListener('submit', this.startKeywordSearch.bind(this));
        }
    }

    switchTab(tabName) {
        this.tabButtons.forEach((btn) => {
            btn.classList.toggle('active', btn.getAttribute('data-searcher-tab') === tabName);
        });
        this.panels.forEach((panel) => {
            panel.classList.toggle('active', panel.getAttribute('data-searcher-panel') === tabName);
        });
    }

    /* ── Section 1: Classic File Search ───────────────── */
    startFileSearch(event) {
        event.preventDefault();

        if (!window.globalConnection || !window.globalConnection.connected) {
            if (window.AmikoAlert) {
                window.AmikoAlert('Connection Required', 'You must connect first using the global connection panel!', 'warning');
            } else {
                alert('You must connect first using the global form above!');
            }
            return;
        }

        this.fileItemsChecked = 0;
        this.fileMatchesCount = 0;

        const params = new URLSearchParams();
        params.append('session_id', window.globalConnection.sessionId);
        params.append('path', this.filePathInput.value);
        params.append('query', this.fileQueryInput.value);
        params.append('target_type', this.fileTargetInput ? this.fileTargetInput.value : 'all');
        params.append('case_sensitive', this.fileCaseInput ? this.fileCaseInput.value : 'false');

        if (this.fileEventSource) {
            this.fileEventSource.close();
        }

        clearTerminal(this.fileTerminal);
        addLogEntry(this.fileTerminal, `Initializing file search for "${this.fileQueryInput.value}" in ${this.filePathInput.value}...`, 'info');

        this.fileBtn.disabled = true;
        this.fileBtn.textContent = 'SEARCHING...';

        this.fileEventSource = new EventSource('/search-files?' + params.toString());

        this.fileEventSource.onmessage = (e) => {
            const data = JSON.parse(e.data);
            let logType = 'log';

            if (data.type === 'info') logType = 'info';
            else if (data.type === 'success') logType = 'success';
            else if (data.type === 'match') {
                logType = 'match';
                this.fileMatchesCount++;
                this.fileItemsChecked++;
            }
            else if (data.type === 'error') logType = 'error';
            else if (data.type === 'log') {
                logType = 'dim';
                this.fileItemsChecked++;
            }
            else if (data.type === 'done') {
                if (typeof data.files_checked === 'number') {
                    this.fileItemsChecked = data.files_checked;
                }
                if (typeof data.matches_found === 'number') {
                    this.fileMatchesCount = data.matches_found;
                }
                this.fileBtn.disabled = false;
                this.fileBtn.textContent = 'FIND FILES';
                this.fileEventSource.close();
                this.showFileSearchSummary();
                return;
            }

            addLogEntry(this.fileTerminal, data.message, logType);
        };

        this.fileEventSource.onerror = () => {
            addLogEntry(this.fileTerminal, 'Connection error or connection lost.', 'error');
            this.fileBtn.disabled = false;
            this.fileBtn.textContent = 'FIND FILES';
            this.fileEventSource.close();
        };
    }

    showFileSearchSummary() {
        addLogEntry(this.fileTerminal, `=== FILE SEARCH SUMMARY ===`, 'success');
        addLogEntry(this.fileTerminal, `Items scanned: ${this.fileItemsChecked}`, 'info');
        addLogEntry(this.fileTerminal, `Matching items found: ${this.fileMatchesCount}`, 'match');
        addLogEntry(this.fileTerminal, `===========================`, 'success');
    }

    /* ── Section 2: Keyword Search in Files ───────────── */
    startKeywordSearch(event) {
        event.preventDefault();

        if (!window.globalConnection || !window.globalConnection.connected) {
            if (window.AmikoAlert) {
                window.AmikoAlert('Connection Required', 'You must connect first using the global connection panel!', 'warning');
            } else {
                alert('You must connect first using the global form above!');
            }
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

        clearTerminal(this.keywordTerminal);
        addLogEntry(this.keywordTerminal, 'Initializing keyword search protocol...', 'info');

        this.keywordBtn.disabled = true;
        this.keywordBtn.textContent = 'SEARCHING...';

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
                this.keywordBtn.disabled = false;
                this.keywordBtn.textContent = 'START SEARCH';
                this.searchEventSource.close();
                this.showKeywordSearchSummary();
                return;
            }

            addLogEntry(this.keywordTerminal, data.message, logType);
        };

        this.searchEventSource.onerror = () => {
            addLogEntry(this.keywordTerminal, 'Connection error or connection lost.', 'error');
            this.keywordBtn.disabled = false;
            this.keywordBtn.textContent = 'START SEARCH';
            this.searchEventSource.close();
        };
    }

    showKeywordSearchSummary() {
        addLogEntry(this.keywordTerminal, `=== KEYWORD SEARCH SUMMARY ===`, 'success');
        addLogEntry(this.keywordTerminal, `Files checked: ${this.searchFilesChecked}`, 'info');
        addLogEntry(this.keywordTerminal, `Matches found: ${this.searchMatchesCount}`, 'match');
        addLogEntry(this.keywordTerminal, `==============================`, 'success');
    }

    handleGlobalConnect() {
        // Ready on connection
    }

    destroy() {
        if (this.fileEventSource) {
            this.fileEventSource.close();
        }
        if (this.searchEventSource) {
            this.searchEventSource.close();
        }
        window.removeEventListener('amikoGlobalConnect', this.globalConnectHandler);
        window.removeEventListener('amikoGlobalDisconnect', this.globalDisconnectHandler);
    }
}

// Register Module (plus legacy alias for saved layouts)
AmikoModules.register('searcher', SearcherModule);
AmikoModules.register('keyword_search', SearcherModule);

