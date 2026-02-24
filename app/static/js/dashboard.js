let schedulerStatuses = {};
let pollTimer = null;
const POLL_INTERVAL = 5000;

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

        animateValue(document.getElementById('stat-stores'), stats.total_stores);
        animateValue(document.getElementById('stat-running'), stats.running_syncs);
        animateValue(document.getElementById('stat-syncs24'), stats.syncs_24h);
        animateValue(document.getElementById('stat-changes24'), stats.changes_24h);

        renderStoreCards(stores);
        renderRecentRuns(stats.recent_runs || []);
        updateCancelButtons(runningSyncs, stores);

        if (stats.running_syncs > 0) {
            startPolling();
        } else {
            stopPolling();
        }
    } catch (err) {
        showToast('Failed to load dashboard: ' + err.message, 'error');
    }
}

function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(loadDashboard, POLL_INTERVAL);
}

function stopPolling() {
    if (!pollTimer) return;
    clearInterval(pollTimer);
    pollTimer = null;
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
            ? '<span class="badge badge-success"><span class="pulse-dot"></span>Scheduler On</span>'
            : '<span class="badge badge-neutral">Scheduler Off</span>';
        const lastSync = store.last_sync_at ? formatDate(store.last_sync_at) : 'Never';
        const pubBadge = store.publication_id
            ? '<span class="badge badge-info">Publication Set</span>'
            : '<span class="badge badge-warning">No Publication</span>';

        return `
            <div class="store-card">
                <h3>${escapeHtml(store.store_name)}</h3>
                <div class="store-url">${escapeHtml(store.store_url)}</div>
                <div class="store-meta">${syncBadge}${pubBadge}</div>
                <div class="store-detail">Last sync: ${lastSync}</div>
                <div class="store-actions">
                    <button class="btn btn-primary btn-sm" onclick="triggerSync(${store.id})">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23,4 23,10 17,10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg>
                        Sync Now
                    </button>
                    <button class="btn btn-danger btn-sm" onclick="cancelSync(${store.id})" style="display:none" id="cancel-btn-${store.id}">Cancel</button>
                    ${sched.running
                        ? `<button class="btn btn-outline btn-sm" onclick="stopScheduler(${store.id})">Stop</button>`
                        : `<button class="btn btn-outline btn-sm" onclick="startScheduler(${store.id})">Start</button>`
                    }
                </div>
            </div>
        `;
    }).join('');
}

function renderRecentRuns(runs) {
    const tbody = document.getElementById('recent-runs');
    if (!runs.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="empty-state">No sync runs yet</td></tr>';
        return;
    }

    tbody.innerHTML = runs.map(run => {
        const isRunning = run.status === 'running';
        const processed = run.products_updated + run.products_published + run.products_unpublished + (run.products_skip_unpublish || 0) + run.products_skipped + run.errors_count;
        const total = run.total_products || 0;
        const statusCell = isRunning && total > 0
            ? `<span class="badge badge-info"><span class="pulse-dot"></span>${processed}/${total}</span>`
            : isRunning
            ? '<span class="badge badge-info"><span class="pulse-dot"></span>running</span>'
            : statusBadge(run.status);
        let durationCell;
        if (run.duration_seconds) {
            durationCell = run.duration_seconds.toFixed(1) + 's';
        } else if (isRunning && run.started_at) {
            const elapsed = (Date.now() - new Date(run.started_at).getTime()) / 1000;
            durationCell = elapsed >= 60 ? Math.floor(elapsed / 60) + 'm ' + Math.floor(elapsed % 60) + 's' : Math.floor(elapsed) + 's';
        } else {
            durationCell = '-';
        }
        return `
        <tr>
            <td>${escapeHtml(run.store_name)}</td>
            <td style="font-size: 12px;">${formatDate(run.started_at)}</td>
            <td>${statusCell}</td>
            <td>${run.products_updated}</td>
            <td>${run.products_published}</td>
            <td>${run.products_unpublished}</td>
            <td>${run.products_skip_unpublish || 0}</td>
            <td>${run.errors_count > 0 ? `<span style="color: var(--error);">${run.errors_count}</span>` : '0'}</td>
            <td>${durationCell}</td>
        </tr>`;
    }).join('');
}

async function triggerSync(storeId) {
    try {
        const res = await fetch(`/api/sync/${storeId}/trigger`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            showToast('Sync started successfully', 'success');
            setTimeout(loadDashboard, 1000);
            startPolling();
        } else {
            showToast(data.error || 'Failed to start sync', 'error');
        }
    } catch (err) {
        showToast('Error: ' + err.message, 'error');
    }
}

async function startScheduler(storeId) {
    try {
        const res = await fetch(`/api/sync/${storeId}/start`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            showToast('Scheduler started', 'success');
            loadDashboard();
        } else {
            showToast(data.error || 'Failed to start scheduler', 'error');
        }
    } catch (err) {
        showToast('Error: ' + err.message, 'error');
    }
}

async function stopScheduler(storeId) {
    try {
        const res = await fetch(`/api/sync/${storeId}/stop`, { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            showToast('Scheduler stopped', 'success');
            loadDashboard();
        } else {
            showToast(data.error || 'Failed to stop scheduler', 'error');
        }
    } catch (err) {
        showToast('Error: ' + err.message, 'error');
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
            showToast(`Cancelled ${count} stuck sync(s)`, 'success');
            loadDashboard();
        } else {
            showToast(data.error || 'Failed to cancel sync', 'error');
        }
    } catch (err) {
        showToast('Error: ' + err.message, 'error');
    }
}

loadDashboard();
