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
        document.getElementById('logs-body').innerHTML = `<tr><td colspan="12" class="empty-state">Error: ${escapeHtml(err.message)}</td></tr>`;
    }
}

function renderLogs(logs) {
    const tbody = document.getElementById('logs-body');
    if (!logs.length) {
        tbody.innerHTML = '<tr><td colspan="12" class="empty-state">No logs found</td></tr>';
        return;
    }

    tbody.innerHTML = logs.map(log => {
        const hasError = log.error_message && log.error_message.trim();
        const errorIcon = hasError
            ? `<button class="btn btn-ghost btn-icon" onclick="showErrorDetail(this)" data-error="${escapeHtml(log.error_message)}" title="View error"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--error)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg></button>`
            : '';
        return `<tr>
            <td style="font-size: 11px; white-space: nowrap;">${formatDate(log.created_at)}</td>
            <td>${escapeHtml(log.store_name)}</td>
            <td class="mono">${escapeHtml(log.product_upc)}</td>
            <td>${escapeHtml(log.product_description || '')}</td>
            <td>${log.quantity_on_hand != null ? log.quantity_on_hand : '-'}</td>
            <td>${log.pending_po_quantity != null ? log.pending_po_quantity : '-'}</td>
            <td>${log.in_progress_quantity != null ? log.in_progress_quantity : '-'}</td>
            <td>${log.committed_quantity != null ? log.committed_quantity : '-'}</td>
            <td>${log.old_quantity != null ? log.old_quantity : '-'}</td>
            <td>${log.new_quantity != null ? log.new_quantity : '-'}</td>
            <td style="white-space: nowrap;">${actionBadge(log.action)}</td>
            <td>${errorIcon}</td>
        </tr>`;
    }).join('');
}

function showErrorDetail(btn) {
    const error = btn.getAttribute('data-error');
    if (!error) return;
    const overlay = document.getElementById('error-detail-modal');
    document.getElementById('error-detail-text').textContent = error;
    overlay.classList.add('active');
}

function closeErrorDetail() {
    document.getElementById('error-detail-modal').classList.remove('active');
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

init();
