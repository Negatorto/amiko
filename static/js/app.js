/*
 * AMIKO - Main Application JavaScript
 * Tab switching and initialization
 */

// Initialize app on page load
document.addEventListener('DOMContentLoaded', function () {
    console.log('AMIKO prioritized - Dashboard Mode Active');
});

window.AmikoAlert = function(title, message, type = 'error') {
    if (!window.WinBox) {
        alert(`${title}: ${message}`);
        return;
    }
    
    // Apply styling classes based on type
    const typeClass = `wb-amiko-${type}`;

    new WinBox({
        title: title,
        html: `<div class="amiko-alert-content">${message}</div>`,
        class: ['wb-amiko', typeClass],
        width: 450,
        height: 200,
        x: "center",
        y: "center",
        top: 70,
        index: 10001,
        modal: true
    });
};

window.AmikoConfirm = function(title, message, onConfirm) {
    if (!window.WinBox) {
        if (confirm(`${title}: ${message}`)) {
            onConfirm();
        }
        return;
    }
    
    const wb = new WinBox({
        title: title,
        html: `
            <div class="amiko-alert-content" style="padding: 20px; text-align: center;">
                <p style="margin-bottom: 20px; color: var(--text-primary); overflow-wrap: break-word; word-break: break-word; width: 100%;">${message}</p>
                <div style="display: flex; gap: 10px; justify-content: center;">
                    <button id="btn-amiko-confirm-yes" class="btn-connect" style="background: rgba(0, 180, 80, 0.15); color: #4cff88; border: 1px solid rgba(0, 180, 80, 0.4); padding: 5px 20px; border-radius: 4px; cursor: pointer;">Yes</button>
                    <button id="btn-amiko-confirm-no" class="btn-disconnect" style="background: rgba(255, 60, 60, 0.1); color: #ff6b6b; border: 1px solid rgba(255, 60, 60, 0.3); padding: 5px 20px; border-radius: 4px; cursor: pointer;">No</button>
                </div>
            </div>
        `,
        class: ['wb-amiko', 'wb-amiko-warning'],
        width: 450,
        height: 200,
        x: "center",
        y: "center",
        top: 70,
        index: 10001,
        modal: true
    });
    
    setTimeout(() => {
        const btnYes = document.getElementById('btn-amiko-confirm-yes');
        const btnNo = document.getElementById('btn-amiko-confirm-no');
        
        if (btnYes) btnYes.addEventListener('click', () => {
            wb.close();
            onConfirm();
        });
        
        if (btnNo) btnNo.addEventListener('click', () => {
            wb.close();
        });
    }, 100);
};

window.AmikoPrompt = function(title, message, defaultValue, onConfirm) {
    if (!window.WinBox) {
        const result = prompt(`${title}\n${message}`, defaultValue);
        if (result !== null) {
            onConfirm(result);
        }
        return;
    }
    
    const wb = new WinBox({
        title: title,
        html: `
            <div class="amiko-alert-content" style="padding: 20px; text-align: center;">
                <p style="margin-bottom: 20px; color: var(--text-primary); overflow-wrap: break-word; word-break: break-word; width: 100%;">${message}</p>
                <input type="text" id="amiko-prompt-input" value="${defaultValue || ''}" style="width: 100%; margin-bottom: 20px; padding: 8px; background: var(--bg-tertiary); color: var(--text-primary); border: 1px solid var(--border-medium); border-radius: 4px;" autofocus>
                <div style="display: flex; gap: 10px; justify-content: center;">
                    <button id="btn-amiko-prompt-ok" class="btn-connect" style="background: rgba(0, 180, 80, 0.15); color: #4cff88; border: 1px solid rgba(0, 180, 80, 0.4); padding: 5px 20px; border-radius: 4px; cursor: pointer;">OK</button>
                    <button id="btn-amiko-prompt-cancel" class="btn-disconnect" style="background: rgba(255, 60, 60, 0.1); color: #ff6b6b; border: 1px solid rgba(255, 60, 60, 0.3); padding: 5px 20px; border-radius: 4px; cursor: pointer;">Cancel</button>
                </div>
            </div>
        `,
        class: ['wb-amiko', 'wb-amiko-warning'],
        width: 450,
        height: 250,
        x: "center",
        y: "center",
        top: 70,
        index: 10001,
        modal: true
    });
    
    setTimeout(() => {
        const input = document.getElementById('amiko-prompt-input');
        const btnOk = document.getElementById('btn-amiko-prompt-ok');
        const btnCancel = document.getElementById('btn-amiko-prompt-cancel');
        
        if (input) {
            input.focus();
            input.select();
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    btnOk.click();
                }
            });
        }
        
        if (btnOk) btnOk.addEventListener('click', () => {
            const val = input ? input.value : '';
            wb.close();
            onConfirm(val);
        });
        
        if (btnCancel) btnCancel.addEventListener('click', () => {
            wb.close();
        });
    }, 100);
};

// Utility: Format timestamp
function formatTimestamp() {
    const now = new Date();
    return now.toLocaleString('it-IT', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

// Utility: Scroll terminal to bottom
function scrollTerminalToBottom(terminalElement) {
    terminalElement.scrollTop = terminalElement.scrollHeight;
}

// Utility: Clear terminal
function clearTerminal(terminalElement) {
    terminalElement.innerHTML = '<div class="log-entry log-dim">// Terminal cleared...</div>';
}

// Utility: Add log entry to terminal
function addLogEntry(terminalElement, message, type = 'log') {
    const entry = document.createElement('div');
    entry.className = `log-entry log-${type}`;
    entry.textContent = `> ${message}`;
    terminalElement.appendChild(entry);
    scrollTerminalToBottom(terminalElement);
}
