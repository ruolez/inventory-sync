let currentPage = 0;
const pageSize = 50;

async function init() {
    await loadStoreFilter();
    await loadHistory();
}

async function loadStoreFilter() {
    try {
        const res = await fetch('/api/stores');
        const stores = await res.json();
        const select = document.getElementById('filter-store');
        for (const store of stores) {
            const opt = document.createElement('option');
            opt.value = store.id;
            opt.textContent = store.store_name;
            select.appendChild(opt);
        }
    } catch (err) {
        console.error('Failed to load stores:', err);
    }
}

async function loadHistory() {
    const storeId = document.getElementById('filter-store').value;
    const status = document.getElementById('filter-status').value;
    const params = new URLSearchParams({
        limit: pageSize,
        offset: currentPage * pageSize,
    });
    if (storeId) params.set('store_id', storeId);
    if (status) params.set('status', status);

    try {
        const res = await fetch(`/api/history?${params}`);
        const runs = await res.json();
        renderHistory(runs);
        updatePagination(runs.length);
    } catch (err) {
        showToast('Failed to load history: ' + err.message, 'error');
    }
}

function renderHistory(runs) {
    const tbody = document.getElementById('history-body');
    if (!runs.length) {
        tbody.innerHTML = '<tr><td colspan="13" class="empty-state">No sync runs found</td></tr>';
        return;
    }

    tbody.innerHTML = runs.map(run => `
        <tr>
            <td class="mono">${run.id}</td>
            <td>${escapeHtml(run.store_name)}</td>
            <td style="font-size: 12px;">${formatDate(run.started_at)}</td>
            <td>${run.status === 'running' ? '<span class="badge badge-info"><span class="pulse-dot"></span>running</span>' : statusBadge(run.status)}</td>
            <td>${run.total_products}</td>
            <td>${run.products_updated}</td>
            <td>${run.products_published}</td>
            <td>${run.products_discontinued || 0}</td>
            <td>${run.products_excluded || 0}</td>
            <td>${run.products_skipped}</td>
            <td>${run.errors_count > 0 ? `<span style="color: var(--error);">${run.errors_count}</span>` : '0'}</td>
            <td>${run.duration_seconds ? run.duration_seconds.toFixed(1) + 's' : '-'}</td>
            <td>
                <button class="btn btn-ghost btn-icon" onclick="deleteRun(${run.id})" title="Delete">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3,6 5,6 21,6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
                </button>
            </td>
        </tr>
    `).join('');
}

async function deleteRun(runId) {
    const confirmed = await confirmDialog('Delete this sync run and its logs?', 'Delete Run');
    if (!confirmed) return;
    try {
        const res = await fetch(`/api/history/${runId}`, { method: 'DELETE' });
        if (res.ok) {
            showToast('Sync run deleted', 'success');
            loadHistory();
        } else {
            const data = await res.json();
            showToast(data.error || 'Failed to delete', 'error');
        }
    } catch (err) {
        showToast('Error: ' + err.message, 'error');
    }
}

function updatePagination(count) {
    document.getElementById('page-info').textContent = `Page ${currentPage + 1}`;
    document.getElementById('prev-btn').disabled = currentPage === 0;
    document.getElementById('next-btn').disabled = count < pageSize;
}

function prevPage() {
    if (currentPage > 0) {
        currentPage--;
        loadHistory();
    }
}

function nextPage() {
    currentPage++;
    loadHistory();
}

init();
