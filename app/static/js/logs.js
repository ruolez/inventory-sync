let currentPage = 0;
const pageSize = 100;
let searchTimeout = null;
let totalLogs = 0;
let totalEstimated = false;

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

function filterParams() {
    const params = new URLSearchParams();
    const storeId = document.getElementById('filter-store').value;
    const action = document.getElementById('filter-action').value;
    const upc = document.getElementById('filter-upc').value.trim();
    if (storeId) params.set('store_id', storeId);
    if (action) params.set('action', action);
    if (upc) params.set('upc', upc);
    return params;
}

// refreshCount: re-query the total (only needed when filters change, not on Prev/Next).
// The count never blocks first paint — logs render as soon as /api/logs returns.
async function loadLogs(refreshCount = true) {
    const params = filterParams();
    params.set('limit', pageSize);
    params.set('offset', currentPage * pageSize);

    if (refreshCount) refreshTotal();

    try {
        const res = await fetch(`/api/logs?${params}`);
        const logs = await res.json();
        renderLogs(logs);
        updatePagination();
    } catch (err) {
        document.getElementById('logs-body').innerHTML = `<tr><td colspan="12" class="empty-state">Error: ${escapeHtml(err.message)}</td></tr>`;
    }
}

async function refreshTotal() {
    try {
        const res = await fetch(`/api/logs/count?${filterParams()}`);
        const data = await res.json();
        totalLogs = data.total;
        totalEstimated = data.estimated;
        updatePagination();
    } catch (err) {
        // leave the prior total in place if the count request fails
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
        const upc = escapeHtml(log.product_upc);
        return `<tr>
            <td class="col-time">${formatDate(log.created_at)}</td>
            <td>${escapeHtml(log.store_name)}</td>
            <td class="mono col-upc" title="${upc}">${upc}</td>
            <td class="col-product">${escapeHtml(log.product_description || '')}</td>
            <td class="col-num">${log.quantity_on_hand != null ? log.quantity_on_hand : '-'}</td>
            <td class="col-num">${log.pending_po_quantity != null ? log.pending_po_quantity : '-'}</td>
            <td class="col-num">${log.in_progress_quantity != null ? log.in_progress_quantity : '-'}</td>
            <td class="col-num">${log.committed_quantity != null ? log.committed_quantity : '-'}</td>
            <td class="col-num">${log.old_quantity != null ? log.old_quantity : '-'}</td>
            <td class="col-num">${log.new_quantity != null ? log.new_quantity : '-'}</td>
            <td>${actionBadge(log.action)}</td>
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

function updatePagination() {
    const totalPages = Math.max(1, Math.ceil(totalLogs / pageSize));
    const totalLabel = `${totalEstimated ? '~' : ''}${totalLogs.toLocaleString()}`;
    document.getElementById('page-info').textContent =
        `Page ${currentPage + 1} of ${totalPages} · ${totalLabel} logs`;
    document.getElementById('prev-btn').disabled = currentPage === 0;
    document.getElementById('next-btn').disabled = currentPage >= totalPages - 1;
}

function prevPage() {
    if (currentPage > 0) {
        currentPage--;
        loadLogs(false);
    }
}

function nextPage() {
    currentPage++;
    loadLogs(false);
}

init();
