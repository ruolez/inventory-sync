let currentPage = 0;
const pageSize = 100;
let searchTimeout = null;

async function init() {
    await loadStoreFilter();
    await loadLogs();
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

async function loadLogs() {
    const storeId = document.getElementById('filter-store').value;
    const action = document.getElementById('filter-action').value;
    const upc = document.getElementById('filter-upc').value.trim();
    const params = new URLSearchParams({
        limit: pageSize,
        offset: currentPage * pageSize,
    });
    if (storeId) params.set('store_id', storeId);
    if (action) params.set('action', action);
    if (upc) params.set('upc', upc);

    try {
        const res = await fetch(`/api/logs?${params}`);
        const logs = await res.json();
        renderLogs(logs);
        updatePagination(logs.length);
    } catch (err) {
        document.getElementById('logs-body').innerHTML = `<tr><td colspan="11" class="empty-state">Error: ${err.message}</td></tr>`;
    }
}

function renderLogs(logs) {
    const tbody = document.getElementById('logs-body');
    if (!logs.length) {
        tbody.innerHTML = '<tr><td colspan="11" class="empty-state">No logs found</td></tr>';
        return;
    }

    tbody.innerHTML = logs.map(log => `
        <tr>
            <td style="font-size: 12px;">${formatDate(log.created_at)}</td>
            <td>${escapeHtml(log.store_name)}</td>
            <td class="mono">${escapeHtml(log.product_upc)}</td>
            <td class="text-truncate">${escapeHtml(log.product_description || '')}</td>
            <td>${log.quantity_on_hand != null ? log.quantity_on_hand : '-'}</td>
            <td>${log.pending_po_quantity != null ? log.pending_po_quantity : '-'}</td>
            <td>${log.in_progress_quantity != null ? log.in_progress_quantity : '-'}</td>
            <td>${log.old_quantity != null ? log.old_quantity : '-'}</td>
            <td>${log.new_quantity != null ? log.new_quantity : '-'}</td>
            <td>${actionBadge(log.action)}</td>
            <td class="text-truncate" title="${escapeHtml(log.error_message || '')}">${escapeHtml(log.error_message || '')}</td>
        </tr>
    `).join('');
}

function actionBadge(action) {
    const map = {
        inventory_update: 'badge-info',
        unpublish: 'badge-warning',
        republish: 'badge-success',
        skip: 'badge-neutral',
        error: 'badge-error',
    };
    return `<span class="badge ${map[action] || 'badge-neutral'}">${action}</span>`;
}

function debounceSearch() {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
        currentPage = 0;
        loadLogs();
    }, 300);
}

function updatePagination(count) {
    document.getElementById('page-info').textContent = `Page ${currentPage + 1}`;
    document.getElementById('prev-btn').disabled = currentPage === 0;
    document.getElementById('next-btn').disabled = count < pageSize;
}

function prevPage() {
    if (currentPage > 0) {
        currentPage--;
        loadLogs();
    }
}

function nextPage() {
    currentPage++;
    loadLogs();
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

init();
