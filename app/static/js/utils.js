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

function formatDate(dateStr) {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleString();
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

function actionBadge(action) {
    const map = {
        inventory_update: 'badge-info',
        inventory_override: 'badge-info',
        discontinued: 'badge-warning',
        unpublish: 'badge-warning',
        republish: 'badge-success',
        skip: 'badge-neutral',
        error: 'badge-error',
    };
    return `<span class="badge ${map[action] || 'badge-neutral'}">${action}</span>`;
}

// --- Toast Notification System ---

let toastCounter = 0;

function showToast(message, type = 'info', duration = 4000) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const id = `toast-${++toastCounter}`;
    const iconMap = {
        success: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>',
        error: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
        info: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
    };

    const toast = document.createElement('div');
    toast.id = id;
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
        <span class="toast-icon">${iconMap[type] || iconMap.info}</span>
        <span class="toast-message">${escapeHtml(message)}</span>
        <button class="toast-close" onclick="dismissToast('${id}')">&times;</button>
    `;

    container.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add('toast-visible'));

    if (duration > 0) {
        setTimeout(() => dismissToast(id), duration);
    }
}

function dismissToast(id) {
    const toast = document.getElementById(id);
    if (!toast) return;
    toast.classList.remove('toast-visible');
    toast.classList.add('toast-exit');
    setTimeout(() => toast.remove(), 300);
}

// --- Confirm Dialog ---

function confirmDialog(message, title = 'Confirm') {
    return new Promise((resolve) => {
        const overlay = document.getElementById('confirm-modal');
        if (!overlay) { resolve(confirm(message)); return; }

        document.getElementById('confirm-title').textContent = title;
        document.getElementById('confirm-message').textContent = message;
        overlay.classList.add('active');

        const confirmBtn = document.getElementById('confirm-yes');
        const cancelBtn = document.getElementById('confirm-no');

        function cleanup() {
            overlay.classList.remove('active');
            confirmBtn.removeEventListener('click', onConfirm);
            cancelBtn.removeEventListener('click', onCancel);
            overlay.removeEventListener('click', onOverlay);
        }

        function onConfirm() { cleanup(); resolve(true); }
        function onCancel() { cleanup(); resolve(false); }
        function onOverlay(e) { if (e.target === overlay) { cleanup(); resolve(false); } }

        confirmBtn.addEventListener('click', onConfirm);
        cancelBtn.addEventListener('click', onCancel);
        overlay.addEventListener('click', onOverlay);
    });
}

// --- Skeleton Loading ---

function skeletonRows(cols, rows = 5) {
    return Array(rows).fill('').map(() =>
        `<tr>${Array(cols).fill('<td><div class="skeleton skeleton-text"></div></td>').join('')}</tr>`
    ).join('');
}

function skeletonCards(count = 3) {
    return Array(count).fill('').map(() =>
        '<div class="skeleton-card"><div class="skeleton skeleton-title"></div><div class="skeleton skeleton-text"></div><div class="skeleton skeleton-text short"></div></div>'
    ).join('');
}

// --- Theme Toggle ---

function initTheme() {
    const saved = localStorage.getItem('theme') || 'dark';
    document.documentElement.setAttribute('data-theme', saved);
    updateThemeIcon(saved);
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
    updateThemeIcon(next);
}

function updateThemeIcon(theme) {
    const btn = document.getElementById('theme-toggle-btn');
    if (!btn) return;
    if (theme === 'dark') {
        btn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>';
        btn.title = 'Switch to light mode';
    } else {
        btn.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
        btn.title = 'Switch to dark mode';
    }
}

// --- Sidebar Toggle ---

function initSidebar() {
    const collapsed = localStorage.getItem('sidebar-collapsed') === 'true';
    if (collapsed) document.getElementById('sidebar')?.classList.add('collapsed');
}

function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;
    sidebar.classList.toggle('collapsed');
    const isCollapsed = sidebar.classList.contains('collapsed');
    localStorage.setItem('sidebar-collapsed', isCollapsed);
    document.documentElement.classList.toggle('sidebar-collapsed', isCollapsed);
}

// --- Count-up Animation ---

function animateValue(el, end, duration = 600) {
    const start = 0;
    const startTime = performance.now();
    const target = parseInt(end) || 0;
    if (target === 0) { el.textContent = '0'; return; }

    function update(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);
        el.textContent = Math.round(eased * target).toLocaleString();
        if (progress < 1) requestAnimationFrame(update);
    }
    requestAnimationFrame(update);
}

// --- Init ---
initTheme();
initSidebar();
