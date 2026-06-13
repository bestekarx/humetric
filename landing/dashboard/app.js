let apiKey = localStorage.getItem('humetric_api_key') || '';
let dashboardData = null;

if (apiKey) {
    document.getElementById('api-key-input').value = apiKey;
    loadDashboard();
}

function login() {
    apiKey = document.getElementById('api-key-input').value.trim();
    localStorage.setItem('humetric_api_key', apiKey);
    loadDashboard();
}

async function apiCall(path, options = {}) {
    const resp = await fetch(path, {
        ...options,
        headers: { 'Content-Type': 'application/json', ...options.headers, Authorization: `Bearer ${apiKey}` },
    });
    return resp.json();
}

async function loadDashboard() {
    showSection('loading');
    try {
        dashboardData = await apiCall('/v1/tenant/dashboard');
        if (dashboardData.error) {
            document.getElementById('error').textContent = dashboardData.error.message;
            showSection('error');
            return;
        }
        renderDashboard();
        showSection('dashboard');
        loadApiKeys();
    } catch (e) {
        document.getElementById('error').textContent = 'API baglanti hatasi';
        showSection('error');
    }
}

function showSection(name) {
    ['login-card', 'loading', 'error', 'dashboard'].forEach(id => {
        document.getElementById(id).classList.add('hidden');
    });
    document.getElementById(name).classList.remove('hidden');
}

function renderDashboard() {
    const d = dashboardData;
    const tierBadge = document.getElementById('tier-badge');
    tierBadge.textContent = d.tier;
    tierBadge.className = 'tier-badge tier-' + d.tier;
    document.getElementById('sub-status').textContent = d.subscription_status;

    if (d.stripe_customer_portal_url) {
        const btn = document.getElementById('portal-btn');
        btn.style.display = 'inline-block';
        btn.dataset.url = d.stripe_customer_portal_url;
    }

    const usage = d.usage_current_month || {};
    document.getElementById('usage-signals').textContent = usage.sinyal_sayisi ?? 0;
    document.getElementById('usage-tokens').textContent = usage.llm_token_sayisi ?? 0;
    document.getElementById('usage-embeddings').textContent = usage.embedding_sayisi ?? 0;

    const limits = d.limits || {};
    document.getElementById('limit-signals').textContent = limits.sinyal_sayisi ?? 'Sinirsiz';
    document.getElementById('limit-entities').textContent = limits.entity_count ?? 'Sinirsiz';
    document.getElementById('limit-packs').textContent = limits.pack_count ?? 'Sinirsiz';
}

// ── API Key Management ──────────────────────────────────────────

async function loadApiKeys() {
    document.getElementById('keys-loading').classList.remove('hidden');
    document.getElementById('keys-table').classList.add('hidden');
    document.getElementById('keys-empty').classList.add('hidden');

    try {
        const result = await apiCall('/v1/api-keys');
        document.getElementById('keys-loading').classList.add('hidden');

        if (result.error) {
            document.getElementById('keys-loading').textContent = 'Anahtarlar yuklenemedi: ' + result.error.message;
            document.getElementById('keys-loading').classList.remove('hidden');
            return;
        }

        const keys = result.api_keys || [];
        if (keys.length === 0) {
            document.getElementById('keys-empty').classList.remove('hidden');
            return;
        }

        const tbody = document.getElementById('keys-tbody');
        tbody.innerHTML = '';
        keys.forEach(k => {
            const now = new Date();
            const expired = k.expires_at && new Date(k.expires_at) < now;
            const status = k.is_revoked ? 'Iptal Edildi' : expired ? 'Suresi Doldu' : 'Aktif';
            const statusClass = k.is_revoked ? 'status-revoked' : expired ? 'status-expired' : 'status-active';
            const rowClass = (k.is_revoked || expired) ? 'revoked-row' : '';
            const lastUsed = k.last_used_at ? new Date(k.last_used_at).toLocaleDateString('tr-TR') : '-';
            const scopes = (k.scopes || []).map(s => `<span class="scope-badge">${s}</span>`).join('');

            tbody.innerHTML += `
                <tr class="${rowClass}">
                    <td>
                        <div style="font-weight:600">${k.label || '-'}</div>
                        <div style="font-size:0.75rem;color:#888;font-family:monospace">${k.prefix}_****</div>
                    </td>
                    <td>${scopes}</td>
                    <td class="${statusClass}">${status}</td>
                    <td style="font-size:0.8rem;color:#666">${lastUsed}</td>
                    <td>
                        ${(!k.is_revoked && !expired) ? `<button class="btn btn-danger btn-sm" onclick="revokeKey(${k.id})">Iptal Et</button>` : ''}
                    </td>
                </tr>
            `;
        });
        document.getElementById('keys-table').classList.remove('hidden');
    } catch (e) {
        document.getElementById('keys-loading').textContent = 'Baglanti hatasi';
        document.getElementById('keys-loading').classList.remove('hidden');
    }
}

async function revokeKey(keyId) {
    if (!confirm('Bu API anahtarini iptal etmek istediginizden emin misiniz?')) return;
    try {
        const result = await apiCall(`/v1/api-keys/${keyId}`, { method: 'DELETE' });
        const msg = document.getElementById('keys-message');
        if (result && result.error) {
            msg.textContent = result.error.message;
            msg.className = 'message error';
        } else {
            msg.textContent = 'Anahtar iptal edildi.';
            msg.className = 'message success';
            loadApiKeys();
        }
    } catch (e) {
        document.getElementById('keys-message').textContent = 'Baglanti hatasi';
        document.getElementById('keys-message').className = 'message error';
    }
}

function openCreateModal() {
    document.getElementById('new-key-label').value = '';
    document.getElementById('new-key-prefix').value = 'hm_live';
    document.getElementById('new-key-scopes').value = 'signals:write, entities:read, entities:write, signals:read, query, packs:read';
    document.getElementById('new-key-days').value = '';
    document.getElementById('new-key-result').classList.add('hidden');
    document.getElementById('new-key-value').textContent = '';
    document.getElementById('create-error').textContent = '';
    document.getElementById('create-btn').classList.remove('hidden');
    document.getElementById('create-modal').classList.remove('hidden');
}

function closeCreateModal() {
    document.getElementById('create-modal').classList.add('hidden');
    loadApiKeys();
}

async function createApiKey() {
    document.getElementById('create-error').textContent = '';
    const label = document.getElementById('new-key-label').value.trim() || null;
    const prefix = document.getElementById('new-key-prefix').value;
    const scopesRaw = document.getElementById('new-key-scopes').value;
    const scopes = scopesRaw.split(',').map(s => s.trim()).filter(Boolean);
    const daysVal = document.getElementById('new-key-days').value.trim();
    const expires_in_days = daysVal ? parseInt(daysVal, 10) : null;

    if (scopes.length === 0) {
        document.getElementById('create-error').textContent = 'En az bir scope secin.';
        return;
    }

    document.getElementById('create-btn').disabled = true;
    try {
        const body = { prefix, scopes, label };
        if (expires_in_days) body.expires_in_days = expires_in_days;

        const result = await apiCall('/v1/api-keys', {
            method: 'POST',
            body: JSON.stringify(body),
        });

        if (result.error) {
            document.getElementById('create-error').textContent = result.error.message;
            document.getElementById('create-btn').disabled = false;
            return;
        }

        document.getElementById('new-key-value').textContent = result.full_key;
        document.getElementById('new-key-result').classList.remove('hidden');
        document.getElementById('create-btn').classList.add('hidden');
    } catch (e) {
        document.getElementById('create-error').textContent = 'Baglanti hatasi';
    }
    document.getElementById('create-btn').disabled = false;
}

function copyNewKey() {
    const key = document.getElementById('new-key-value').textContent;
    navigator.clipboard.writeText(key).then(() => {
        const btn = event.target;
        btn.textContent = 'Kopyalandi!';
        setTimeout(() => btn.textContent = 'Kopyala', 2000);
    });
}

// ── Portal & Billing ────────────────────────────────────────────

function openPortal() {
    const url = document.getElementById('portal-btn').dataset.url;
    if (url) window.open(url, '_blank');
}

async function upgrade(tier) {
    try {
        const result = await apiCall(`/v1/billing/checkout?tier=${tier}`, { method: 'POST' });
        if (result.checkout_url) {
            window.open(result.checkout_url, '_blank');
        } else {
            alert(result.error?.message || 'Yukseltme hatasi');
        }
    } catch (e) {
        alert('Baglanti hatasi');
    }
}
