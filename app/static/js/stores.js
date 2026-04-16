let currentLocationsStoreId = null;
let fetchedLocations = [];

async function loadStores() {
    try {
        const res = await fetch('/api/stores');
        const stores = await res.json();
        renderStores(stores);
    } catch (err) {
        showToast('Failed to load stores: ' + err.message, 'error');
    }
}

function renderStores(stores) {
    const container = document.getElementById('stores-list');
    if (!stores.length) {
        container.innerHTML = '<div class="empty-state"><h3>No stores yet</h3><p>Click "Add Store" to connect a Shopify store.</p></div>';
        return;
    }

    container.innerHTML = '<div class="card-grid">' + stores.map(store => `
        <div class="store-card">
            <h3>${escapeHtml(store.store_name)}</h3>
            <div class="store-url">${escapeHtml(store.store_url)}</div>
            <div class="store-meta">
                <span class="badge ${store.auth_method === 'oauth_client_credentials' ? 'badge-info' : 'badge-neutral'}">
                    ${store.auth_method === 'oauth_client_credentials' ? 'OAuth' : 'Token'}
                </span>
                <span class="badge ${store.sync_enabled ? 'badge-success' : 'badge-neutral'}">
                    ${store.sync_enabled ? 'Sync Enabled' : 'Sync Disabled'}
                </span>
                <span class="badge ${store.publication_id ? 'badge-info' : 'badge-warning'}">
                    ${store.publication_id ? 'Publication Set' : 'No Publication'}
                </span>
                <span class="badge badge-neutral">Every ${store.sync_interval_hours}h</span>
            </div>
            <div class="store-detail">Last sync: ${store.last_sync_at ? formatDate(store.last_sync_at) : 'Never'}</div>
            <div class="store-actions">
                <button class="btn btn-outline btn-sm" onclick="testConnection(${store.id})">Test</button>
                <button class="btn btn-outline btn-sm" onclick="openLocations(${store.id})">Locations</button>
                <button class="btn btn-outline btn-sm" onclick="fetchPublication(${store.id})">Publication</button>
                <button class="btn btn-outline btn-sm" onclick="openEditModal(${store.id}, '${escapeAttr(store.store_name)}', '${escapeAttr(store.store_url)}', ${store.sync_interval_hours}, '${store.auth_method || 'legacy'}')">Edit</button>
                <button class="btn btn-danger btn-sm" onclick="deleteStore(${store.id}, '${escapeAttr(store.store_name)}')">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3,6 5,6 21,6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
                </button>
            </div>
        </div>
    `).join('') + '</div>';
}

function openAddModal() {
    document.getElementById('modal-title').textContent = 'Add Store';
    document.getElementById('store-id').value = '';
    document.getElementById('store-name').value = '';
    document.getElementById('store-url').value = '';
    document.getElementById('admin-token').value = '';
    document.getElementById('oauth-client-id').value = '';
    document.getElementById('oauth-client-secret').value = '';
    document.getElementById('auth-method').value = 'legacy';
    document.getElementById('sync-interval').value = '6';
    toggleAuthFields();
    document.getElementById('store-modal').classList.add('active');
}

function openEditModal(id, name, url, interval, authMethod) {
    document.getElementById('modal-title').textContent = 'Edit Store';
    document.getElementById('store-id').value = id;
    document.getElementById('store-name').value = name;
    document.getElementById('store-url').value = url;
    document.getElementById('admin-token').value = '';
    document.getElementById('oauth-client-id').value = '';
    document.getElementById('oauth-client-secret').value = '';
    document.getElementById('auth-method').value = authMethod || 'legacy';
    document.getElementById('sync-interval').value = interval;
    toggleAuthFields();
    document.getElementById('store-modal').classList.add('active');
}

function closeModal() {
    document.getElementById('store-modal').classList.remove('active');
}

function toggleAuthFields() {
    const method = document.getElementById('auth-method').value;
    document.getElementById('legacy-auth-fields').style.display = method === 'legacy' ? '' : 'none';
    document.getElementById('oauth-auth-fields').style.display = method === 'oauth_client_credentials' ? '' : 'none';
}

async function saveStore() {
    const id = document.getElementById('store-id').value;
    const authMethod = document.getElementById('auth-method').value;
    const data = {
        store_name: document.getElementById('store-name').value.trim(),
        store_url: document.getElementById('store-url').value.trim(),
        sync_interval_hours: parseInt(document.getElementById('sync-interval').value),
        auth_method: authMethod,
    };

    if (!data.store_name || !data.store_url) {
        showToast('Store name and URL are required', 'error');
        return;
    }

    if (authMethod === 'oauth_client_credentials') {
        const clientId = document.getElementById('oauth-client-id').value.trim();
        const clientSecret = document.getElementById('oauth-client-secret').value.trim();
        if (!id && (!clientId || !clientSecret)) {
            showToast('Client ID and Client Secret are required for new stores', 'error');
            return;
        }
        if (clientId) data.oauth_client_id = clientId;
        if (clientSecret) data.oauth_client_secret = clientSecret;
    } else {
        const token = document.getElementById('admin-token').value.trim();
        if (!id && !token) {
            showToast('Admin access token is required for new stores', 'error');
            return;
        }
        if (token) data.admin_access_token = token;
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
            showToast(id ? 'Store updated' : 'Store created', 'success');
            loadStores();
        } else {
            showToast(result.error || 'Failed to save store', 'error');
        }
    } catch (err) {
        showToast('Error: ' + err.message, 'error');
    }
}

async function deleteStore(id, name) {
    const confirmed = await confirmDialog(`Delete store "${name}"? This will also delete all sync history and logs.`, 'Delete Store');
    if (!confirmed) return;
    try {
        const res = await fetch(`/api/stores/${id}`, { method: 'DELETE' });
        if (res.ok) {
            showToast('Store deleted', 'success');
            loadStores();
        } else {
            const data = await res.json();
            showToast(data.error || 'Failed to delete', 'error');
        }
    } catch (err) {
        showToast('Error: ' + err.message, 'error');
    }
}

async function testConnection(storeId) {
    try {
        showToast('Testing connection...', 'info');
        const res = await fetch(`/api/stores/${storeId}/test`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showToast(`Connected to ${data.name} (${data.domain})`, 'success');
        } else {
            showToast('Connection failed: ' + (data.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        showToast('Error: ' + err.message, 'error');
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
            document.getElementById('locations-list').innerHTML = `<div class="empty-state"><p>${escapeHtml(locations.error || 'Failed to load')}</p></div>`;
        }
    } catch (err) {
        document.getElementById('locations-list').innerHTML = `<div class="empty-state"><p>${escapeHtml(err.message)}</p></div>`;
    }
}

function renderLocations(locations) {
    if (!locations.length) {
        document.getElementById('locations-list').innerHTML = '<div class="empty-state"><p>No locations found</p></div>';
        return;
    }

    document.getElementById('locations-list').innerHTML = locations.map((loc, i) => `
        <div class="location-item">
            <input type="radio" name="location" id="loc-${i}" value="${loc.id}" ${loc.is_active_saved ? 'checked' : ''}>
            <label for="loc-${i}">
                <div class="location-name">${escapeHtml(loc.name)}</div>
                <div class="location-id">${loc.id}</div>
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
        showToast('Please select a location', 'error');
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
            showToast('Location saved', 'success');
        } else {
            const data = await res.json();
            showToast(data.error || 'Failed to save', 'error');
        }
    } catch (err) {
        showToast('Error: ' + err.message, 'error');
    }
}

async function fetchPublication(storeId) {
    try {
        showToast('Fetching publication ID...', 'info');
        const res = await fetch(`/api/stores/${storeId}/publication`, { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(`Publication saved: ${data.publication.name} (${data.publication.id})`, 'success');
            loadStores();
        } else {
            showToast(data.error || 'Failed to fetch publication', 'error');
        }
    } catch (err) {
        showToast('Error: ' + err.message, 'error');
    }
}

loadStores();
