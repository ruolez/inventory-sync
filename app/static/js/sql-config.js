async function loadConfigs() {
    try {
        const res = await fetch('/api/config/sql');
        const configs = await res.json();
        for (const config of configs) {
            populateForm(config);
        }
    } catch (err) {
        showToast('Failed to load configs: ' + err.message, 'error');
    }
}

function populateForm(config) {
    const prefix = config.config_key === 's2s' ? 's2s' : 'admin';
    document.getElementById(`${prefix}-host`).value = config.host || '';
    document.getElementById(`${prefix}-port`).value = config.port || 1433;
    document.getElementById(`${prefix}-database`).value = config.database_name || '';
    document.getElementById(`${prefix}-username`).value = config.username || '';
}

async function saveConfig(configKey) {
    const prefix = configKey === 's2s' ? 's2s' : 'admin';
    const data = {
        config_key: configKey,
        host: document.getElementById(`${prefix}-host`).value.trim(),
        port: parseInt(document.getElementById(`${prefix}-port`).value) || 1433,
        database_name: document.getElementById(`${prefix}-database`).value.trim(),
        username: document.getElementById(`${prefix}-username`).value.trim(),
        password: document.getElementById(`${prefix}-password`).value.trim(),
    };

    if (!data.host || !data.database_name || !data.username || !data.password) {
        showToast('All fields are required', 'error');
        return;
    }

    try {
        const res = await fetch('/api/config/sql', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        const result = await res.json();
        if (res.ok) {
            showToast(`${configKey.toUpperCase()} config saved`, 'success');
        } else {
            showToast(result.error || 'Failed to save', 'error');
        }
    } catch (err) {
        showToast('Error: ' + err.message, 'error');
    }
}

async function testConnection(configKey) {
    const endpoint = configKey === 's2s' ? '/api/config/test-s2s' : '/api/config/test-admin';
    const statusId = configKey === 's2s' ? 's2s-status' : 'admin-status';

    try {
        showToast('Testing connection...', 'info');
        const res = await fetch(endpoint, { method: 'POST' });
        const data = await res.json();
        const statusEl = document.getElementById(statusId);
        if (data.success) {
            showToast(`${configKey.toUpperCase()}: Connection successful`, 'success');
            statusEl.innerHTML = '<span class="status-dot connected"></span><span style="color: var(--success);">Connected</span>';
        } else {
            showToast(`${configKey.toUpperCase()}: ${data.error || 'Connection failed'}`, 'error');
            statusEl.innerHTML = '<span class="status-dot failed"></span><span style="color: var(--error);">Failed</span>';
        }
    } catch (err) {
        showToast('Error: ' + err.message, 'error');
    }
}

loadConfigs();
