let currentRange = '7d';
let charts = {};

function getChartColors() {
    const style = getComputedStyle(document.documentElement);
    return {
        text: style.getPropertyValue('--text-secondary').trim(),
        textTertiary: style.getPropertyValue('--text-tertiary').trim(),
        border: style.getPropertyValue('--border').trim(),
        accent: style.getPropertyValue('--accent').trim(),
        success: style.getPropertyValue('--success').trim(),
        warning: style.getPropertyValue('--warning').trim(),
        error: style.getPropertyValue('--error').trim(),
        info: style.getPropertyValue('--info').trim(),
        bg2: style.getPropertyValue('--bg-2').trim(),
    };
}

function chartDefaults() {
    const c = getChartColors();
    return {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: {
                labels: { color: c.text, font: { family: "'DM Sans', sans-serif", size: 11 }, boxWidth: 12, padding: 12 }
            },
            tooltip: {
                backgroundColor: c.bg2,
                titleColor: c.text,
                bodyColor: c.text,
                borderColor: c.border,
                borderWidth: 1,
                titleFont: { family: "'DM Sans', sans-serif" },
                bodyFont: { family: "'DM Sans', sans-serif" },
                padding: 10,
                cornerRadius: 6,
            }
        },
        scales: {
            x: {
                ticks: { color: c.textTertiary, font: { family: "'DM Sans', sans-serif", size: 11 } },
                grid: { color: c.border },
                border: { color: c.border },
            },
            y: {
                ticks: { color: c.textTertiary, font: { family: "'DM Sans', sans-serif", size: 11 } },
                grid: { color: c.border },
                border: { color: c.border },
                beginAtZero: true,
            }
        }
    };
}

function destroyChart(key) {
    if (charts[key]) {
        charts[key].destroy();
        charts[key] = null;
    }
}

function formatDay(dateStr) {
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function buildParams() {
    const params = new URLSearchParams();
    params.set('range', currentRange);
    const storeId = document.getElementById('store-filter').value;
    if (storeId) params.set('store_id', storeId);
    return params.toString();
}

function setRange(range) {
    currentRange = range;
    document.querySelectorAll('.range-btn').forEach(btn => {
        btn.classList.toggle('active', btn.textContent.trim() === range);
    });
    reloadAll();
}

async function loadStores() {
    try {
        const res = await fetch('/api/stores');
        const stores = await res.json();
        const select = document.getElementById('store-filter');
        const current = select.value;
        select.innerHTML = '<option value="">All Stores</option>';
        stores.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.id;
            opt.textContent = s.store_name;
            select.appendChild(opt);
        });
        if (current) select.value = current;
    } catch (err) {
        // Silently handle — stores dropdown is optional
    }
}

async function reloadAll() {
    const q = buildParams();
    try {
        const [summaryRes, stockRes, syncRes, actionRes, moversRes, errorsRes] = await Promise.all([
            fetch(`/api/analytics/summary?${q}`),
            fetch(`/api/analytics/stock-trend?${q}`),
            fetch(`/api/analytics/sync-activity?${q}`),
            fetch(`/api/analytics/action-distribution?${q}`),
            fetch(`/api/analytics/top-movers?${q}`),
            fetch(`/api/analytics/errors?${q}`),
        ]);

        const summary = await summaryRes.json();
        const stock = await stockRes.json();
        const sync = await syncRes.json();
        const actions = await actionRes.json();
        const movers = await moversRes.json();
        const errors = await errorsRes.json();

        renderKPIs(summary);
        renderStockTrend(stock);
        renderSyncActivity(sync.daily_activity);
        renderActionDistribution(actions);
        renderDurationTrend(sync.duration_trend);
        renderTopMovers(movers.top_movers);
        renderFrequentOOS(movers.frequent_oos);
        renderRecentErrors(errors.recent_errors);
        renderErrorTrend(errors.error_trend);
    } catch (err) {
        showToast('Failed to load analytics: ' + err.message, 'error');
    }
}

function renderKPIs(data) {
    animateValue(document.getElementById('kpi-total-syncs'), data.total_syncs);
    animateValue(document.getElementById('kpi-updated'), data.products_updated);
    animateValue(document.getElementById('kpi-published'), data.products_published);

    const errorRateEl = document.getElementById('kpi-error-rate');
    errorRateEl.textContent = data.error_rate + '%';

    const successEl = document.getElementById('kpi-success-rate');
    successEl.textContent = data.success_rate + '% success rate';
}

function renderStockTrend(data) {
    const c = getChartColors();
    destroyChart('stockTrend');
    const ctx = document.getElementById('chart-stock-trend');
    if (!data.length) {
        ctx.parentElement.innerHTML = '<div class="empty-state"><p>No stock movement data</p></div>';
        return;
    }
    charts.stockTrend = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.map(d => formatDay(d.day)),
            datasets: [
                {
                    label: 'Published',
                    data: data.map(d => d.published),
                    borderColor: c.success,
                    backgroundColor: c.success + '20',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 3,
                    pointHoverRadius: 5,
                },
                {
                    label: 'Unpublished',
                    data: data.map(d => d.unpublished),
                    borderColor: c.warning,
                    backgroundColor: c.warning + '20',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 3,
                    pointHoverRadius: 5,
                },
            ],
        },
        options: chartDefaults(),
    });
}

function renderSyncActivity(data) {
    const c = getChartColors();
    destroyChart('syncActivity');
    const ctx = document.getElementById('chart-sync-activity');
    if (!data.length) {
        ctx.parentElement.innerHTML = '<div class="empty-state"><p>No sync activity data</p></div>';
        return;
    }
    const defaults = chartDefaults();
    defaults.scales.x.stacked = true;
    defaults.scales.y.stacked = true;

    charts.syncActivity = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.map(d => formatDay(d.day)),
            datasets: [
                { label: 'Updated', data: data.map(d => d.updated), backgroundColor: c.info, borderRadius: 2 },
                { label: 'Published', data: data.map(d => d.published), backgroundColor: c.success, borderRadius: 2 },
                { label: 'Unpublished', data: data.map(d => d.unpublished), backgroundColor: c.warning, borderRadius: 2 },
                { label: 'Skipped', data: data.map(d => d.skipped), backgroundColor: c.textTertiary, borderRadius: 2 },
                { label: 'Errors', data: data.map(d => d.errors), backgroundColor: c.error, borderRadius: 2 },
            ],
        },
        options: defaults,
    });
}

function renderActionDistribution(data) {
    const c = getChartColors();
    destroyChart('actionDist');
    const ctx = document.getElementById('chart-action-dist');
    if (!data.length) {
        ctx.parentElement.innerHTML = '<div class="empty-state"><p>No action data</p></div>';
        return;
    }
    const colorMap = {
        inventory_update: c.info,
        inventory_override: c.info,
        discontinued: c.warning,
        skip: c.textTertiary,
        republish: c.success,
        unpublish: c.warning,
        error: c.error,
    };

    charts.actionDist = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: data.map(d => d.action),
            datasets: [{
                data: data.map(d => d.count),
                backgroundColor: data.map(d => colorMap[d.action] || c.accent),
                borderWidth: 0,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '60%',
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { color: c.text, font: { family: "'DM Sans', sans-serif", size: 11 }, boxWidth: 12, padding: 10 }
                },
                tooltip: {
                    backgroundColor: c.bg2,
                    titleColor: c.text,
                    bodyColor: c.text,
                    borderColor: c.border,
                    borderWidth: 1,
                    padding: 10,
                    cornerRadius: 6,
                }
            }
        },
    });
}

function renderDurationTrend(data) {
    const c = getChartColors();
    destroyChart('duration');
    const ctx = document.getElementById('chart-duration');
    if (!data.length) {
        ctx.parentElement.innerHTML = '<div class="empty-state"><p>No duration data</p></div>';
        return;
    }

    charts.duration = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.map(d => formatDay(d.started_at)),
            datasets: [{
                label: 'Duration (s)',
                data: data.map(d => d.duration_seconds),
                borderColor: c.accent,
                backgroundColor: c.accent + '20',
                fill: true,
                tension: 0.3,
                pointRadius: 4,
                pointHoverRadius: 6,
                pointBackgroundColor: data.map(d => d.status === 'failed' ? c.error : c.accent),
                pointBorderColor: data.map(d => d.status === 'failed' ? c.error : c.accent),
            }],
        },
        options: chartDefaults(),
    });
}

function renderTopMovers(data) {
    const tbody = document.getElementById('table-top-movers');
    if (!data.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="empty-state">No inventory updates in this period</td></tr>';
        return;
    }
    tbody.innerHTML = data.map(d => `
        <tr>
            <td class="mono">${escapeHtml(d.product_upc)}</td>
            <td class="text-truncate">${escapeHtml(d.product_description || '-')}</td>
            <td><strong>${Number(d.total_change).toLocaleString()}</strong></td>
            <td>${d.update_count}</td>
        </tr>
    `).join('');
}

function renderFrequentOOS(data) {
    const tbody = document.getElementById('table-frequent-oos');
    if (!data.length) {
        tbody.innerHTML = '<tr><td colspan="3" class="empty-state">No out-of-stock events in this period</td></tr>';
        return;
    }
    tbody.innerHTML = data.map(d => `
        <tr>
            <td class="mono">${escapeHtml(d.product_upc)}</td>
            <td class="text-truncate">${escapeHtml(d.product_description || '-')}</td>
            <td><span class="badge badge-warning">${d.oos_count}</span></td>
        </tr>
    `).join('');
}

function renderRecentErrors(data) {
    const tbody = document.getElementById('table-recent-errors');
    if (!data.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="empty-state">No errors in this period</td></tr>';
        return;
    }
    tbody.innerHTML = data.map(d => `
        <tr>
            <td style="font-size: 12px; white-space: nowrap;">${formatDate(d.created_at)}</td>
            <td>${escapeHtml(d.store_name)}</td>
            <td class="mono">${escapeHtml(d.product_upc)}</td>
            <td class="text-truncate" style="max-width: 300px;" title="${escapeAttr(d.error_message || '')}">${escapeHtml(d.error_message || '-')}</td>
        </tr>
    `).join('');
}

function renderErrorTrend(data) {
    const c = getChartColors();
    destroyChart('errorTrend');
    const ctx = document.getElementById('chart-error-trend');
    if (!data.length) {
        ctx.parentElement.innerHTML = '<div class="empty-state"><p>No error trend data</p></div>';
        return;
    }

    charts.errorTrend = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.map(d => formatDay(d.day)),
            datasets: [{
                label: 'Error Rate (%)',
                data: data.map(d => parseFloat(d.error_rate)),
                borderColor: c.error,
                backgroundColor: c.error + '20',
                fill: true,
                tension: 0.3,
                pointRadius: 3,
                pointHoverRadius: 5,
            }],
        },
        options: chartDefaults(),
    });
}

// Theme reactivity — re-render charts when theme changes
const themeObserver = new MutationObserver(() => {
    reloadAll();
});
themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

// Init
loadStores();
reloadAll();
