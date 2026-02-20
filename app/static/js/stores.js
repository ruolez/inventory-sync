let currentLocationsStoreId = null;
let fetchedLocations = [];

async function loadStores() {
    try {
        const res = await fetch('/api/stores');
        const stores = await res.json();
        renderStores(stores);
    } catch (err) {
        showAlert('Failed to load stores: ' + err.message, 'error');
    }
}

function renderStores(stores) {
    const container = document.getElementById('stores-list');
    if (!stores.length) {
        container.innerHTML = '<div class="empty-state"><h3>No stores yet</h3><p>Click "Add Store" to connect a Shopify store.</p></div>';
        return;
    }

    container.innerHTML = stores.map(store => `
        <div class="store-card" style="margin-bottom: 12px;">
            <h3>${escapeHtml(store.store_name)}</h3>
            <div class="store-url">${escapeHtml(store.store_url)}</div>
            <div class="store-meta">
                <span class="badge ${store.sync_enabled ? 'badge-success' : 'badge-neutral'}">
                    ${store.sync_enabled ? 'Sync Enabled' : 'Sync Disabled'}
                </span>
                <span class="badge ${store.publication_id ? 'badge-info' : 'badge-warning'}">
                    ${store.publication_id ? 'Publication Set' : 'No Publication'}
                </span>
                <span class="badge badge-neutral">Every ${store.sync_interval_hours}h</span>
            </div>
            <div style="font-size: 13px; color: var(--text-secondary); margin-bottom: 12px;">
                Last sync: ${store.last_sync_at ? formatDate(store.last_sync_at) : 'Never'}
            </div>
            <div class="store-actions">
                <button class="btn btn-outline btn-sm" onclick="testConnection(${store.id})">Test Connection</button>
                <button class="btn btn-outline btn-sm" onclick="openLocations(${store.id})">Locations</button>
                <button class="btn btn-outline btn-sm" onclick="fetchPublication(${store.id})">Fetch Publication</button>
                <button class="btn btn-outline btn-sm" onclick="openEditModal(${store.id}, '${escapeAttr(store.store_name)}', '${escapeAttr(store.store_url)}', ${store.sync_interval_hours})">Edit</button>
                <button class="btn btn-danger btn-sm" onclick="deleteStore(${store.id}, '${escapeAttr(store.store_name)}')">Delete</button>
            </div>
        </div>
    `).join('');
}

function openAddModal() {
    document.getElementById('modal-title').textContent = 'Add Store';
    document.getElementById('store-id').value = '';
    document.getElementById('store-name').value = '';
    document.getElementById('store-url').value = '';
    document.getElementById('admin-token').value = '';
    document.getElementById('sync-interval').value = '6';
    document.getElementById('store-modal').classList.add('active');
}

function openEditModal(id, name, url, interval) {
    document.getElementById('modal-title').textContent = 'Edit Store';
    document.getElementById('store-id').value = id;
    document.getElementById('store-name').value = name;
    document.getElementById('store-url').value = url;
    document.getElementById('admin-token').value = '';
    document.getElementById('sync-interval').value = interval;
    document.getElementById('store-modal').classList.add('active');
}

function closeModal() {
    document.getElementById('store-modal').classList.remove('active');
}

async function saveStore() {
    const id = document.getElementById('store-id').value;
    const data = {
        store_name: document.getElementById('store-name').value.trim(),
        store_url: document.getElementById('store-url').value.trim(),
        sync_interval_hours: parseInt(document.getElementById('sync-interval').value),
    };
    const token = document.getElementById('admin-token').value.trim();
    if (token) data.admin_access_token = token;

    if (!data.store_name || !data.store_url) {
        showAlert('Store name and URL are required', 'error');
        return;
    }
    if (!id && !token) {
        showAlert('Admin access token is required for new stores', 'error');
        return;
    }

    try {
        const url = id ? `/api/stores/${id}` : '/api/stores';
        const method = id ? 'PUT' : 'POST';
        const res = await fetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        const result = await res.json();
        if (res.ok) {
            closeModal();
            showAlert(id ? 'Store updated' : 'Store created', 'success');
            loadStores();
        } else {
            showAlert(result.error || 'Failed to save store', 'error');
        }
    } catch (err) {
        showAlert('Error: ' + err.message, 'error');
    }
}

async function deleteStore(id, name) {
    if (!confirm(`Delete store "${name}"? This will also delete all sync history and logs.`)) return;
    try {
        const res = await fetch(`/api/stores/${id}`, { method: 'DELETE' });
        if (res.ok) {
            showAlert('Store deleted', 'success');
            loadStores();
        } else {
            const data = await res.json();
            showAlert(data.error || 'Failed to delete', 'error');
        }
    } catch (err) {
        showAlert('Error: ' + err.message, 'error');
    }
}

async function testConnection(storeId) {
    try {
        showAlert('Testing connection...', 'success');
        const res = await fetch(`/api/stores/${storeId}/test`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showAlert(`Connected to ${data.name} (${data.domain})`, 'success');
        } else {
            showAlert('Connection failed: ' + (data.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        showAlert('Error: ' + err.message, 'error');
    }
}

async function openLocations(storeId) {
    currentLocationsStoreId = storeId;
    document.getElementById('locations-modal').classList.add('active');
    document.getElementById('locations-list').innerHTML = '<div class="loading-text"><span class="spinner"></span> Loading locations from Shopify...</div>';

    try {
        const res = await fetch(`/api/stores/${storeId}/locations`);
        const locations = await res.json();
        if (res.ok) {
            fetchedLocations = locations;
            renderLocations(locations);
        } else {
            document.getElementById('locations-list').innerHTML = `<div class="alert alert-error show">${locations.error || 'Failed to load'}</div>`;
        }
    } catch (err) {
        document.getElementById('locations-list').innerHTML = `<div class="alert alert-error show">${err.message}</div>`;
    }
}

function renderLocations(locations) {
    if (!locations.length) {
        document.getElementById('locations-list').innerHTML = '<div class="empty-state"><p>No locations found</p></div>';
        return;
    }

    document.getElementById('locations-list').innerHTML = locations.map((loc, i) => `
        <div style="padding: 10px 0; border-bottom: 1px solid var(--outline); display: flex; align-items: center; gap: 12px;">
            <input type="radio" name="location" id="loc-${i}" value="${loc.id}"
                ${loc.is_active_saved ? 'checked' : ''}>
            <label for="loc-${i}" style="flex: 1; cursor: pointer;">
                <div style="font-weight: 500;">${escapeHtml(loc.name)}</div>
                <div style="font-size: 12px; color: var(--text-secondary);">${loc.id}</div>
            </label>
            <span class="badge ${loc.is_active ? 'badge-success' : 'badge-neutral'}">
                ${loc.is_active ? 'Active' : 'Inactive'}
            </span>
        </div>
    `).join('');
}

function closeLocationsModal() {
    document.getElementById('locations-modal').classList.remove('active');
    currentLocationsStoreId = null;
}

async function saveLocations() {
    const selected = document.querySelector('input[name="location"]:checked');
    if (!selected) {
        showAlert('Please select a location', 'error');
        return;
    }

    const loc = fetchedLocations.find(l => l.id === selected.value);
    const locations = [{
        location_id: selected.value,
        location_name: loc ? loc.name : '',
        is_active: true,
    }];

    try {
        const res = await fetch(`/api/stores/${currentLocationsStoreId}/locations`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ locations }),
        });
        if (res.ok) {
            closeLocationsModal();
            showAlert('Location saved', 'success');
        } else {
            const data = await res.json();
            showAlert(data.error || 'Failed to save', 'error');
        }
    } catch (err) {
        showAlert('Error: ' + err.message, 'error');
    }
}

async function fetchPublication(storeId) {
    try {
        showAlert('Fetching publication ID...', 'success');
        const res = await fetch(`/api/stores/${storeId}/publication`, { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.success) {
            showAlert(`Publication saved: ${data.publication.name} (${data.publication.id})`, 'success');
            loadStores();
        } else {
            showAlert(data.error || 'Failed to fetch publication', 'error');
        }
    } catch (err) {
        showAlert('Error: ' + err.message, 'error');
    }
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleString();
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function escapeAttr(str) {
    if (!str) return '';
    return str.replace(/'/g, "\\'").replace(/"/g, '\\"');
}

function showAlert(message, type) {
    const alert = document.getElementById('alert');
    alert.className = `alert alert-${type} show`;
    alert.textContent = message;
    setTimeout(() => alert.classList.remove('show'), 5000);
}

loadStores();
