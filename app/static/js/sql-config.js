async function loadConfigs() {
    try {
        const res = await fetch('/api/config/sql');
        const configs = await res.json();
        for (const config of configs) {
            populateForm(config);
        }
    } catch (err) {
        showAlert('Failed to load configs: ' + err.message, 'error');
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
        showAlert('All fields are required', 'error');
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
            showAlert(`${configKey.toUpperCase()} config saved`, 'success');
        } else {
            showAlert(result.error || 'Failed to save', 'error');
        }
    } catch (err) {
        showAlert('Error: ' + err.message, 'error');
    }
}

async function testConnection(configKey) {
    const endpoint = configKey === 's2s' ? '/api/config/test-s2s' : '/api/config/test-admin';
    try {
        showAlert('Testing connection...', 'success');
        const res = await fetch(endpoint, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showAlert(`${configKey.toUpperCase()}: Connection successful`, 'success');
        } else {
            showAlert(`${configKey.toUpperCase()}: ${data.error || 'Connection failed'}`, 'error');
        }
    } catch (err) {
        showAlert('Error: ' + err.message, 'error');
    }
}

function showAlert(message, type) {
    const alert = document.getElementById('alert');
    alert.className = `alert alert-${type} show`;
    alert.textContent = message;
    setTimeout(() => alert.classList.remove('show'), 5000);
}

loadConfigs();
