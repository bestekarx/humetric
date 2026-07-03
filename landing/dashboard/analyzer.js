let dashboardToken = localStorage.getItem('humetric_dashboard_token') || '';
let currentSession = null;
let pendingImages = [];
let pollTimer = null;
const MAX_IMAGES = 5;
const MAX_IMAGE_MB = 5;

if (!dashboardToken) {
    window.location = '/dashboard';
} else {
    init();
}

function logout() {
    localStorage.removeItem('humetric_dashboard_token');
    window.location = '/dashboard';
}

async function apiCall(path, options = {}) {
    const resp = await fetch(path, {
        ...options,
        headers: { 'Content-Type': 'application/json', ...options.headers, Authorization: `Bearer ${dashboardToken}` },
    });
    if (resp.status === 401) {
        logout();
        return { error: { message: 'Oturum suresi doldu, lutfen tekrar giris yapin.' } };
    }
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok && !data.error) {
        return { error: { message: `Istek basarisiz (${resp.status})` } };
    }
    return data;
}

function showSection(name) {
    ['error', 'session-list', 'new-session', 'processing', 'findings', 'done'].forEach(id => {
        document.getElementById(id).classList.add('hidden');
    });
    document.getElementById(name).classList.remove('hidden');
    if (name !== 'processing' && pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}

function showError(message) {
    document.getElementById('error-text').textContent = message;
    showSection('error');
}

function init() {
    const params = new URLSearchParams(window.location.search);
    const sessionId = params.get('session');
    if (sessionId) {
        openSession(parseInt(sessionId, 10));
    } else {
        showSection('session-list');
        loadSessionList();
    }
}

// ── Session list ──────────────────────────────────────────────

async function loadSessionList() {
    document.getElementById('session-list-loading').classList.remove('hidden');
    document.getElementById('session-list-items').innerHTML = '';
    document.getElementById('session-list-empty').classList.add('hidden');

    const result = await apiCall('/v1/analyzer/sessions');
    document.getElementById('session-list-loading').classList.add('hidden');

    if (result.error) {
        showError(result.error.message);
        return;
    }

    const items = result.items || [];
    if (items.length === 0) {
        document.getElementById('session-list-empty').classList.remove('hidden');
        return;
    }

    const statusLabels = {
        pending_payment: 'Odeme bekliyor',
        processing: 'Devam ediyor',
        findings_ready: 'Bulgular hazir',
        completed: 'Tamamlandi',
        failed: 'Hata',
    };

    const container = document.getElementById('session-list-items');
    items.forEach(s => {
        const div = document.createElement('div');
        div.className = 'session-item';
        div.innerHTML = `
            <div>
                <div class="title">${escapeHtml(s.title)}</div>
                <div class="meta">${new Date(s.created_at).toLocaleString('tr-TR')}${s.pack_key ? ' &middot; pack: ' + escapeHtml(s.pack_key) : ''}</div>
            </div>
            <div style="display:flex;align-items:center;gap:0.5rem">
                <span class="status-badge status-${s.status}">${statusLabels[s.status] || s.status}</span>
                <button class="btn btn-outline btn-sm" onclick="openSession(${s.id})">Ac</button>
            </div>
        `;
        container.appendChild(div);
    });
}

function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
}

// ── New session ───────────────────────────────────────────────

function showNewSessionForm() {
    document.getElementById('new-message').value = '';
    document.getElementById('new-schema-text').value = '';
    document.getElementById('new-schema-format').value = 'sql';
    document.getElementById('new-session-error').textContent = '';
    document.getElementById('new-images').value = '';
    document.getElementById('image-chips').innerHTML = '';
    pendingImages = [];
    showSection('new-session');
}

function handleImageInput(event) {
    const files = Array.from(event.target.files || []);
    const errBox = document.getElementById('new-session-error');
    errBox.textContent = '';

    for (const file of files) {
        if (pendingImages.length >= MAX_IMAGES) {
            errBox.textContent = `En fazla ${MAX_IMAGES} gorsel yukleyebilirsiniz.`;
            break;
        }
        if (file.size > MAX_IMAGE_MB * 1024 * 1024) {
            errBox.textContent = `${file.name}: ${MAX_IMAGE_MB}MB sinirini asiyor.`;
            continue;
        }
        if (!/^image\/(png|jpeg|webp|gif)$/.test(file.type)) {
            errBox.textContent = `${file.name}: desteklenmeyen format.`;
            continue;
        }
        const reader = new FileReader();
        reader.onload = () => {
            const dataUrl = reader.result;
            const base64 = dataUrl.substring(dataUrl.indexOf(',') + 1);
            pendingImages.push({ name: file.name, media_type: file.type, data_b64: base64 });
            renderImageChips();
        };
        reader.readAsDataURL(file);
    }
}

function renderImageChips() {
    const row = document.getElementById('image-chips');
    row.innerHTML = '';
    pendingImages.forEach((img, idx) => {
        const chip = document.createElement('div');
        chip.className = 'image-chip';
        chip.innerHTML = `<span>${escapeHtml(img.name)}</span><button onclick="removeImage(${idx})">&times;</button>`;
        row.appendChild(chip);
    });
}

function removeImage(idx) {
    pendingImages.splice(idx, 1);
    renderImageChips();
}

async function startAnalysis() {
    const errBox = document.getElementById('new-session-error');
    errBox.textContent = '';
    const message = document.getElementById('new-message').value.trim();
    if (message.length < 10) {
        errBox.textContent = 'Aciklama en az 10 karakter olmali.';
        return;
    }

    const body = { message, images: pendingImages };
    const schemaText = document.getElementById('new-schema-text').value.trim();
    if (schemaText) {
        body.schema_text = schemaText;
        body.schema_format = document.getElementById('new-schema-format').value;
    }

    const btn = document.getElementById('start-analysis-btn');
    btn.disabled = true;
    const result = await apiCall('/v1/analyzer/sessions', { method: 'POST', body: JSON.stringify(body) });
    btn.disabled = false;

    if (result.error) {
        errBox.textContent = result.error.message;
        return;
    }

    if (result.checkout_url) {
        window.location = result.checkout_url;
        return;
    }

    currentSession = result;
    renderSession();
}

// ── Session view / polling ───────────────────────────────────

async function openSession(id) {
    const result = await apiCall(`/v1/analyzer/sessions/${id}`);
    if (result.error) {
        showError(result.error.message);
        return;
    }
    currentSession = result;
    renderSession();
}

function renderSession() {
    const s = currentSession;
    if (!s) return;

    if (s.status === 'pending_payment') {
        document.getElementById('processing-text').textContent = 'Odeme bekleniyor.';
        document.getElementById('go-to-checkout-btn').style.display = 'inline-block';
        showSection('processing');
        startPolling();
    } else if (s.status === 'processing') {
        document.getElementById('processing-text').textContent = 'Analiz devam ediyor — pazar arastirmasi dakikalar surebilir.';
        document.getElementById('go-to-checkout-btn').style.display = 'none';
        showSection('processing');
        startPolling();
    } else if (s.status === 'findings_ready') {
        renderFindings(s);
        showSection('findings');
    } else if (s.status === 'completed') {
        document.getElementById('done-pack-key').textContent = s.pack_key || '-';
        showSection('done');
    } else if (s.status === 'failed') {
        showError(s.error || 'Analiz basarisiz oldu.');
    }
}

function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
        if (!currentSession) return;
        const result = await apiCall(`/v1/analyzer/sessions/${currentSession.id}`);
        if (result.error) return;
        currentSession = result;
        if (result.status !== 'pending_payment' && result.status !== 'processing') {
            clearInterval(pollTimer);
            pollTimer = null;
        }
        renderSession();
    }, 3000);
}

async function goToCheckout() {
    if (!currentSession) return;
    const result = await apiCall(`/v1/analyzer/sessions/${currentSession.id}/checkout`, { method: 'POST' });
    if (result.error) {
        showError(result.error.message);
        return;
    }
    window.location = result.checkout_url;
}

// ── Findings view ─────────────────────────────────────────────

function renderFindings(s) {
    const reportBox = document.getElementById('report-blocks');
    reportBox.innerHTML = '';
    (s.report || []).forEach(section => {
        const div = document.createElement('div');
        div.className = 'report-block';
        div.innerHTML = `<div class="kind">${escapeHtml(section.kind)}</div>${escapeHtml(section.text)}`;
        reportBox.appendChild(div);
    });

    const f = s.findings || {};
    const oq = document.getElementById('open-questions-list');
    oq.innerHTML = '';
    (f.open_questions || []).forEach(q => {
        const li = document.createElement('li');
        li.textContent = q;
        oq.appendChild(li);
    });
    document.getElementById('open-questions-card').style.display = (f.open_questions || []).length ? '' : 'none';

    document.getElementById('refine-message').value = '';
    document.getElementById('refine-error').textContent = '';
    const refineBtn = document.getElementById('refine-btn');
    const remaining = s.max_refines - s.refine_count;
    if (remaining <= 0) {
        refineBtn.disabled = true;
        refineBtn.textContent = 'Refine hakki kalmadi';
    } else {
        refineBtn.disabled = false;
        refineBtn.textContent = `Yanitla ve Gelistir (kalan: ${remaining})`;
    }

    document.getElementById('f-entity-type').value = f.entity_type || '';
    document.getElementById('f-label').value = f.label || '';
    document.getElementById('f-summary').value = f.summary || '';
    document.getElementById('f-extraction-prompt').value = f.extraction_prompt || '';
    document.getElementById('f-curation-prompt').value = f.curation_prompt || '';
    document.getElementById('f-market-notes').value = f.market_notes || '';
    document.getElementById('f-required-fields').value = JSON.stringify(f.required_fields || [], null, 2);
    document.getElementById('pack-key-input').value = s.pack_key || f.entity_type || '';
    document.getElementById('findings-message').textContent = '';

    renderMetricsList(f.metrics || []);
}

function renderMetricsList(metrics) {
    const list = document.getElementById('metrics-list');
    list.innerHTML = '';
    metrics.forEach((m, idx) => list.appendChild(buildMetricRow(m, idx)));
}

function buildMetricRow(m, idx) {
    const div = document.createElement('div');
    div.className = 'metric-row';
    div.dataset.idx = idx;
    div.innerHTML = `
        <button class="btn btn-danger btn-sm remove-btn" onclick="removeMetricRow(this)">&times;</button>
        <div class="row">
            <div><label>Key</label><input type="text" class="m-key" value="${escapeHtml(m.key || '')}"></div>
            <div><label>Label</label><input type="text" class="m-label" value="${escapeHtml(m.label || '')}"></div>
            <div><label>Tip</label><input type="text" class="m-type" value="${escapeHtml(m.type || 'float')}"></div>
        </div>
        <div class="form-group">
            <label>Extraction Prompt</label>
            <textarea class="m-prompt" style="min-height:50px">${escapeHtml(m.prompt || '')}</textarea>
        </div>
        <div class="row">
            <div><label>Varsayilan Guven (0-1)</label><input type="text" class="m-confidence" value="${m.default_confidence ?? 0.7}"></div>
            <div><label>Rationale</label><input type="text" class="m-rationale" value="${escapeHtml(m.rationale || '')}"></div>
        </div>
        <div class="sensitive-row">
            <input type="checkbox" class="m-sensitive" ${m.sensitive ? 'checked' : ''}>
            <label>Hassas veri (KVKK)</label>
            <input type="text" class="m-consent-scope" placeholder="consent scope (opsiyonel)" value="${escapeHtml(m.requires_consent_scope || '')}" style="flex:1">
        </div>
    `;
    return div;
}

function addMetricRow() {
    const list = document.getElementById('metrics-list');
    list.appendChild(buildMetricRow({ key: '', label: '', type: 'float', prompt: '', default_confidence: 0.7 }, list.children.length));
}

function removeMetricRow(btn) {
    btn.closest('.metric-row').remove();
}

function collectFindingsFromForm() {
    const metrics = Array.from(document.querySelectorAll('#metrics-list .metric-row')).map(row => ({
        key: row.querySelector('.m-key').value.trim(),
        label: row.querySelector('.m-label').value.trim(),
        type: row.querySelector('.m-type').value.trim() || 'float',
        prompt: row.querySelector('.m-prompt').value.trim(),
        default_confidence: parseFloat(row.querySelector('.m-confidence').value) || 0.7,
        sensitive: row.querySelector('.m-sensitive').checked,
        requires_consent_scope: row.querySelector('.m-consent-scope').value.trim() || null,
        rationale: row.querySelector('.m-rationale').value.trim(),
    }));

    let requiredFields = [];
    try {
        requiredFields = JSON.parse(document.getElementById('f-required-fields').value || '[]');
    } catch (e) {
        requiredFields = currentSession.findings.required_fields || [];
    }

    return {
        summary: document.getElementById('f-summary').value,
        entity_type: document.getElementById('f-entity-type').value.trim(),
        label: document.getElementById('f-label').value.trim(),
        required_fields: requiredFields,
        metrics,
        extraction_prompt: document.getElementById('f-extraction-prompt').value,
        curation_prompt: document.getElementById('f-curation-prompt').value,
        open_questions: (currentSession.findings && currentSession.findings.open_questions) || [],
        market_notes: document.getElementById('f-market-notes').value,
    };
}

async function saveFindingsEdits() {
    const msg = document.getElementById('findings-message');
    msg.textContent = '';
    const body = collectFindingsFromForm();
    const result = await apiCall(`/v1/analyzer/sessions/${currentSession.id}/findings`, {
        method: 'PUT', body: JSON.stringify(body),
    });
    if (result.error) {
        msg.textContent = result.error.message;
        msg.className = 'message error';
        return;
    }
    currentSession = result;
    msg.textContent = 'Kaydedildi.';
    msg.className = 'message success';
}

async function submitRefine() {
    const errBox = document.getElementById('refine-error');
    errBox.textContent = '';
    const message = document.getElementById('refine-message').value.trim();
    if (!message) {
        errBox.textContent = 'Lutfen bir yanit yazin.';
        return;
    }
    const result = await apiCall(`/v1/analyzer/sessions/${currentSession.id}/refine`, {
        method: 'POST', body: JSON.stringify({ message }),
    });
    if (result.error) {
        errBox.textContent = result.error.message;
        return;
    }
    currentSession = result;
    renderSession();
}

async function createPack() {
    const msg = document.getElementById('findings-message');
    msg.textContent = '';
    await saveFindingsEdits();

    const packKey = document.getElementById('pack-key-input').value.trim() || null;
    const result = await apiCall(`/v1/analyzer/sessions/${currentSession.id}/create-pack`, {
        method: 'POST', body: JSON.stringify({ pack_key: packKey }),
    });
    if (result.error) {
        msg.textContent = result.error.message;
        msg.className = 'message error';
        return;
    }
    currentSession = result;
    renderSession();
}

async function deleteCurrentSession() {
    if (!currentSession) return;
    if (!confirm('Bu analizi silmek istediginizden emin misiniz? Bu islem geri alinamaz.')) return;
    const result = await apiCall(`/v1/analyzer/sessions/${currentSession.id}`, { method: 'DELETE' });
    if (result && result.error) {
        alert(result.error.message);
        return;
    }
    currentSession = null;
    showSection('session-list');
    loadSessionList();
}
