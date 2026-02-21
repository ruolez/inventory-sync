let schedulerStatuses = {};

async function loadDashboard() {
    try {
        const [statsRes, storesRes, statusRes, runningRes] = await Promise.all([
            fetch('/api/dashboard/stats'),
            fetch('/api/stores'),
            fetch('/api/sync/status'),
            fetch('/api/history?status=running&limit=50'),
        ]);
        const stats = await statsRes.json();
        const stores = await storesRes.json();
        schedulerStatuses = await statusRes.json();
        const runningSyncs = await runningRes.json();

        document.getElementById('stat-stores').textContent = stats.total_stores;
        document.getElementById('stat-running').textContent = stats.running_syncs;
        document.getElementById('stat-syncs24').textContent = stats.syncs_24h;
        document.getElementById('stat-changes24').textContent = stats.changes_24h;

        renderStoreCards(stores);
        renderRecentRuns(stats.recent_runs || []);
        updateCancelButtons(runningSyncs, stores);
    } catch (err) {
        showAlert('Failed to load dashboard: ' + err.message, 'error');
    }
}

function renderStoreCards(stores) {
    const grid = document.getElementById('stores-grid');
    if (!stores.length) {
        grid.innerHTML = '<div class="empty-state"><h3>No stores configured</h3><p>Add a Shopify store to get started.</p><a href="/stores" class="btn btn-primary">Add Store</a></div>';
        return;
    }

    grid.innerHTML = stores.map(store => {
        const sched = schedulerStatuses[store.id] || {};
        const syncBadge = sched.running
            ? '<span class="badge badge-success">Scheduler On</span>'
            : '<span class="badge badge-neutral">Scheduler Off</span>';
        const lastSync = store.last_sync_at
            ? formatDate(store.last_sync_at)
            : 'Never';
        const pubBadge = store.publication_id
            ? '<span class="badge badge-info">Publication Set</span>'
            : '<span class="badge badge-warning">No Publication</span>';

        return `
            <div class="store-card">
                <h3>${escapeHtml(store.store_name)}</h3>
                <div class="store-url">${escapeHtml(store.store_url)}</div>
                <div class="store-meta">
                    ${syncBadge}
                    ${pubBadge}
                </div>
                <div style="font-size: 13px; color: var(--text-secondary); margin-bottom: 12px;">
                    Last sync: ${lastSync}
                </div>
                <div class="store-actions">
                    <button class="btn btn-primary btn-sm" onclick="triggerSync(${store.id})">Sync Now</button>
                    <button class="btn btn-danger btn-sm" onclick="cancelSync(${store.id})" style="display:none" id="cancel-btn-${store.id}">Cancel Sync</button>
                    ${sched.running
                        ? `<button class="btn btn-outline btn-sm" onclick="stopScheduler(${store.id})">Stop Scheduler</button>`
                        : `<button class="btn btn-outline btn-sm" onclick="startScheduler(${store.id})">Start Scheduler</button>`
                    }
                </div>
            </div>
        `;
    }).join('');
}

function renderRecentRuns(runs) {
    const tbody = document.getElementById('recent-runs');
    if (!runs.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="empty-state">No sync runs yet</td></tr>';
        return;
    }

    tbody.innerHTML = runs.map(run => `
        <tr>
            <td>${escapeHtml(run.store_name)}</td>
            <td>${formatDate(run.started_at)}</td>
            <td>${statusBadge(run.status)}</td>
            <td>${run.products_updated}</td>
            <td>${run.products_published}</td>
            <td>${run.products_unpublished}</td>
            <td>${run.errors_count}</td>
            <td>${run.duration_seconds ? run.duration_seconds.toFixed(1) + 's' : '-'}</td>
        </tr>
    `).join('');
}

async function triggerSync(storeId) {
    try {
        const res = await fetch(`/api/sync/${storeId}/trigger`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            showAlert('Sync started successfully', 'success');
            setTimeout(loadDashboard, 2000);
        } else {
            showAlert(data.error || 'Failed to start sync', 'error');
        }
    } catch (err) {
        showAlert('Error: ' + err.message, 'error');
    }
}

async function startScheduler(storeId) {
    try {
        const res = await fetch(`/api/sync/${storeId}/start`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            showAlert('Scheduler started', 'success');
            loadDashboard();
        } else {
            showAlert(data.error || 'Failed to start scheduler', 'error');
        }
    } catch (err) {
        showAlert('Error: ' + err.message, 'error');
    }
}

async function stopScheduler(storeId) {
    try {
        const res = await fetch(`/api/sync/${storeId}/stop`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            showAlert('Scheduler stopped', 'success');
            loadDashboard();
        } else {
            showAlert(data.error || 'Failed to stop scheduler', 'error');
        }
    } catch (err) {
        showAlert('Error: ' + err.message, 'error');
    }
}

function updateCancelButtons(runs, stores) {
    const runningStoreIds = new Set(runs.map(r => r.store_id));
    for (const store of stores) {
        const btn = document.getElementById(`cancel-btn-${store.id}`);
        if (btn) {
            btn.style.display = runningStoreIds.has(store.id) ? '' : 'none';
        }
    }
}

async function cancelSync(storeId) {
    try {
        const res = await fetch(`/api/sync/${storeId}/cancel`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            const count = data.cancelled || 0;
            showAlert(`Cancelled ${count} stuck sync(s)`, 'success');
            loadDashboard();
        } else {
            showAlert(data.error || 'Failed to cancel sync', 'error');
        }
    } catch (err) {
        showAlert('Error: ' + err.message, 'error');
    }
}

function statusBadge(status) {
    const map = {
        success: 'badge-success',
        partial: 'badge-warning',
        failed: 'badge-error',
        running: 'badge-info',
    };
    return `<span class="badge ${map[status] || 'badge-neutral'}">${status}</span>`;
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    const d = new Date(dateStr);
    return d.toLocaleString();
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function showAlert(message, type) {
    const alert = document.getElementById('alert');
    alert.className = `alert alert-${type} show`;
    alert.textContent = message;
    setTimeout(() => alert.classList.remove('show'), 5000);
}

loadDashboard();
