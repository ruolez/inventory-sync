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
        showAlert('Failed to load history: ' + err.message, 'error');
    }
}

function renderHistory(runs) {
    const tbody = document.getElementById('history-body');
    if (!runs.length) {
        tbody.innerHTML = '<tr><td colspan="12" class="empty-state">No sync runs found</td></tr>';
        return;
    }

    tbody.innerHTML = runs.map(run => `
        <tr>
            <td class="mono">${run.id}</td>
            <td>${escapeHtml(run.store_name)}</td>
            <td>${formatDate(run.started_at)}</td>
            <td>${statusBadge(run.status)}</td>
            <td>${run.total_products}</td>
            <td>${run.products_updated}</td>
            <td>${run.products_published}</td>
            <td>${run.products_unpublished}</td>
            <td>${run.products_skipped}</td>
            <td>${run.errors_count > 0 ? `<span style="color: var(--error);">${run.errors_count}</span>` : '0'}</td>
            <td>${run.duration_seconds ? run.duration_seconds.toFixed(1) + 's' : '-'}</td>
            <td>
                <button class="btn btn-danger btn-sm" onclick="deleteRun(${run.id})">Delete</button>
            </td>
        </tr>
    `).join('');
}

async function deleteRun(runId) {
    if (!confirm('Delete this sync run and its logs?')) return;
    try {
        const res = await fetch(`/api/history/${runId}`, { method: 'DELETE' });
        if (res.ok) {
            showAlert('Sync run deleted', 'success');
            loadHistory();
        } else {
            const data = await res.json();
            showAlert(data.error || 'Failed to delete', 'error');
        }
    } catch (err) {
        showAlert('Error: ' + err.message, 'error');
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
    return new Date(dateStr).toLocaleString();
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

init();
