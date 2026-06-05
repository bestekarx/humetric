let apiKey = localStorage.getItem('humetric_api_key') || '';
let dashboardData = null;
let apiKeyVisible = false;

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
        headers: { ...options.headers, Authorization: `Bearer ${apiKey}` },
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
    document.getElementById('api-prefix').textContent = d.api_key_prefix || '-';

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

async function rotateApiKey() {
    try {
        const result = await apiCall('/v1/tenant/rotate-api-key', { method: 'POST' });
        if (result.api_key) {
            document.getElementById('api-key-show').value = result.api_key;
            apiKeyVisible = true;
            apiKey = result.api_key;
            localStorage.setItem('humetric_api_key', apiKey);
            document.getElementById('api-prefix').textContent = result.api_key_prefix;
            document.getElementById('rotate-message').textContent = 'API key yenilendi! Bu anahtari kaydedin.';
            document.getElementById('rotate-message').className = 'message success';
        } else {
            document.getElementById('rotate-message').textContent = result.error?.message || 'Hata';
            document.getElementById('rotate-message').className = 'message error';
        }
    } catch (e) {
        document.getElementById('rotate-message').textContent = 'Baglanti hatasi';
        document.getElementById('rotate-message').className = 'message error';
    }
}

function toggleApiKey() {
    apiKeyVisible = !apiKeyVisible;
    const input = document.getElementById('api-key-show');
    const btn = document.getElementById('show-hide-btn');
    if (apiKeyVisible) {
        input.value = apiKey;
        btn.textContent = 'Gizle';
    } else {
        input.value = '';
        btn.textContent = 'Goster';
    }
}

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
