let searchTimeout = null;

async function loadExcludedProducts() {
    try {
        const res = await fetch('/api/excluded-products');
        const products = await res.json();
        renderProducts(products);
    } catch (err) {
        showToast('Failed to load excluded products: ' + err.message, 'error');
    }
}

function renderProducts(products) {
    const tbody = document.getElementById('excluded-body');
    const badge = document.getElementById('count-badge');
    badge.textContent = products.length;

    if (!products.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty-state">No excluded products</td></tr>';
        return;
    }

    tbody.innerHTML = products.map(p => `
        <tr>
            <td class="mono">${escapeHtml(p.product_upc)}</td>
            <td>${escapeHtml(p.product_description || '-')}</td>
            <td>${escapeHtml(p.reason || '-')}</td>
            <td style="font-size: 12px;">${formatDate(p.created_at)}</td>
            <td>
                <button class="btn btn-ghost btn-icon" onclick="deleteExclusion(${p.id}, '${escapeAttr(p.product_upc)}')" title="Remove">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3,6 5,6 21,6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
                </button>
            </td>
        </tr>
    `).join('');
}

async function addExclusion() {
    const searchInput = document.getElementById('search-input');
    const selectedUpc = document.getElementById('selected-upc').value;
    const selectedDesc = document.getElementById('selected-description').value;
    const reason = document.getElementById('reason-input').value.trim();

    const upc = selectedUpc || searchInput.value.trim();
    if (!upc) {
        showToast('Enter a UPC or search for a product', 'error');
        return;
    }

    try {
        const res = await fetch('/api/excluded-products', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                product_upc: upc,
                product_description: selectedDesc || null,
                reason: reason || null,
            }),
        });

        if (res.status === 409) {
            showToast('This product is already excluded', 'error');
            return;
        }

        if (!res.ok) {
            const data = await res.json();
            showToast(data.error || 'Failed to add exclusion', 'error');
            return;
        }

        showToast('Product excluded from sync', 'success');
        searchInput.value = '';
        document.getElementById('selected-upc').value = '';
        document.getElementById('selected-description').value = '';
        document.getElementById('reason-input').value = '';
        loadExcludedProducts();
    } catch (err) {
        showToast('Error: ' + err.message, 'error');
    }
}

async function deleteExclusion(id, upc) {
    const confirmed = await confirmDialog(`Remove "${upc}" from exclusions? It will be synced again on the next run.`, 'Remove Exclusion');
    if (!confirmed) return;
    try {
        const res = await fetch(`/api/excluded-products/${id}`, { method: 'DELETE' });
        if (res.ok) {
            showToast('Exclusion removed', 'success');
            loadExcludedProducts();
        } else {
            const data = await res.json();
            showToast(data.error || 'Failed to remove', 'error');
        }
    } catch (err) {
        showToast('Error: ' + err.message, 'error');
    }
}

function searchProducts() {
    const query = document.getElementById('search-input').value.trim();
    const dropdown = document.getElementById('autocomplete-dropdown');

    if (query.length < 2) {
        dropdown.classList.remove('active');
        return;
    }

    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(async () => {
        try {
            const res = await fetch(`/api/excluded-products/search?q=${encodeURIComponent(query)}`);
            const results = await res.json();
            if (!results.length) {
                dropdown.classList.remove('active');
                return;
            }
            dropdown.innerHTML = results.map(r => `
                <div class="autocomplete-item" onclick="selectProduct('${escapeAttr(r.upc)}', '${escapeAttr(r.description || '')}')">
                    <span class="autocomplete-upc">${escapeHtml(r.upc)}</span>
                    <span class="autocomplete-desc">${escapeHtml(r.description || '')}</span>
                </div>
            `).join('');
            dropdown.classList.add('active');
        } catch (err) {
            dropdown.classList.remove('active');
        }
    }, 300);
}

function selectProduct(upc, description) {
    document.getElementById('search-input').value = upc + (description ? ' — ' + description : '');
    document.getElementById('selected-upc').value = upc;
    document.getElementById('selected-description').value = description;
    document.getElementById('autocomplete-dropdown').classList.remove('active');
}

document.getElementById('search-input').addEventListener('input', searchProducts);

document.addEventListener('click', function(e) {
    const dropdown = document.getElementById('autocomplete-dropdown');
    if (!e.target.closest('.autocomplete-wrapper')) {
        dropdown.classList.remove('active');
    }
});

document.getElementById('search-input').addEventListener('focus', function() {
    document.getElementById('selected-upc').value = '';
    document.getElementById('selected-description').value = '';
});

loadExcludedProducts();
