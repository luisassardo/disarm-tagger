/* DISARM Tagger — frontend. Renders the analysis bundle from /api/suggest into
   the ARGUS desk shell. i18n is bridged from node.js (window.ArgusLang). */
(function () {
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const lang = () => (window.ArgusLang ? window.ArgusLang.get() : 'en');
  const hydrateIcons = (root) => { if (window.ArgusIcons) window.ArgusIcons.hydrate(root || document); };

  const TX = {
    en: { analyzing: 'Analyzing case', confirm: 'Confirm', confirmed: 'Confirmed', remove: 'Remove',
          suggested: 'suggested', readMore: 'Read reports', whyTactic: 'Why it matters',
          noResults: 'No matches. Describe concrete behaviours (fake accounts, narrative, amplification).',
          done: (n) => `${n} techniques`, addresses: 'addresses', linkPh: 'https://…' },
    es: { analyzing: 'Analizando caso', confirm: 'Confirmar', confirmed: 'Confirmada', remove: 'Quitar',
          suggested: 'sugerida', readMore: 'Leer reportes', whyTactic: 'Por qué importa',
          noResults: 'Sin coincidencias. Describe comportamientos concretos (cuentas falsas, narrativa, amplificación).',
          done: (n) => `${n} técnicas`, addresses: 'cubre', linkPh: 'https://…' },
  };
  const tx = () => TX[lang()] || TX.en;

  const state = { framework: null, bundle: null, confirmed: new Set() };

  function esc(s) { return (s || '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  /* ---------- links list ---------- */
  function addLinkRow(val) {
    const wrap = $('#links-wrap');
    const row = document.createElement('div');
    row.className = 'link-row';
    row.innerHTML = `<input type="url" placeholder="${tx().linkPh}" value="${esc(val || '')}">
      <button class="rm" data-ico="copy" title="remove">×</button>`;
    row.querySelector('.rm').textContent = '×';
    row.querySelector('.rm').addEventListener('click', () => row.remove());
    wrap.appendChild(row);
  }
  function getLinks() { return $$('#links-wrap input').map((i) => i.value.trim()).filter(Boolean); }

  /* ---------- analyze ---------- */
  async function analyze() {
    const description = $('#case-input').value.trim();
    const links = getLinks();
    if (!description && !links.length) { $('#case-input').focus(); return; }
    const btn = $('#analyze');
    btn.disabled = true;
    $('#status-line').innerHTML = `<span class="loading"><span class="live-dot"></span>${tx().analyzing}…</span>`;
    try {
      const r = await fetch('/api/suggest', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description, links, lang: lang() }),
      });
      const data = await r.json();
      state.bundle = data;
      state.confirmed = new Set((data.techniques || []).map((t) => t.technique_id));
      $('#engine-badge').textContent = data.badge || '';
      renderAll();
      updateExport();
      $('#status-line').textContent = data.techniques && data.techniques.length ? tx().done(data.techniques.length) : '';
      if (!data.techniques || !data.techniques.length) {
        $('#col-techniques').innerHTML = `<p class="empty-hint">${tx().noResults}</p>`;
      }
    } catch (e) {
      $('#status-line').textContent = 'Error: ' + e.message;
    } finally { btn.disabled = false; }
  }

  /* ---------- expandable item helper ---------- */
  function wireExpand(root) {
    $$('.aitem .ai-head', root).forEach((head) => {
      head.addEventListener('click', (ev) => {
        if (ev.target.closest('.mini') || ev.target.closest('a')) return;
        head.closest('.aitem').classList.toggle('open');
      });
    });
  }

  /* ---------- render bundle ---------- */
  function renderAll() {
    const b = state.bundle; if (!b) return;
    renderTechniques(b.techniques || []);
    renderTactics(b.tactics || []);
    renderCounters(b.counters || []);
    renderCases(b.similar_cases || []);
    renderResources(b.resources || []);
    $('#n-tech').textContent = (b.techniques || []).length || '';
    $('#n-tac').textContent = (b.tactics || []).length || '';
    $('#n-cnt').textContent = (b.counters || []).length || '';
    hydrateIcons();
  }

  function renderTechniques(techs) {
    const col = $('#col-techniques');
    if (!techs.length) { return; }
    col.innerHTML = '';
    techs.forEach((t) => {
      const on = state.confirmed.has(t.technique_id);
      const conf = (t.confidence || '').toLowerCase();
      const el = document.createElement('div');
      el.className = 'aitem tech' + (on ? ' confirmed' : '');
      el.innerHTML = `
        <div class="ai-head">
          <span class="ai-id">${t.technique_id}</span>
          <span class="ai-name">${esc(t.name)}</span>
          ${conf ? `<span class="conf-pill ${conf}">${conf}</span>` : ''}
          <span class="ai-chev">+</span>
        </div>
        ${t.rationale ? `<div class="ai-why">${esc(t.rationale)}</div>` : ''}
        <div class="ai-body">${esc(t.summary || '')}</div>
        <div class="ai-actions">
          <button class="mini toggle ${on ? 'on' : ''}">${on ? '✓ ' + tx().confirmed : tx().confirm}</button>
        </div>`;
      el.querySelector('.toggle').addEventListener('click', () => {
        if (state.confirmed.has(t.technique_id)) state.confirmed.delete(t.technique_id);
        else state.confirmed.add(t.technique_id);
        renderTechniques(techs); updateExport(); hydrateIcons();
      });
      col.appendChild(el);
    });
    wireExpand(col);
  }

  function renderTactics(tactics) {
    const col = $('#col-tactics');
    if (!tactics.length) { return; }
    col.innerHTML = '';
    tactics.forEach((t) => {
      const el = document.createElement('div');
      el.className = 'aitem tactic';
      el.innerHTML = `
        <div class="ai-head">
          <span class="ai-id">${t.tactic_id}</span>
          <span class="ai-name">${esc(t.name)}</span>
          <span class="ai-chev">+</span>
        </div>
        <div class="ai-why">${esc(t.phase_id)} ${esc(t.phase_name)} · ${t.technique_ids.length}×</div>
        <div class="ai-body">${esc(t.summary || '')}</div>`;
      col.appendChild(el);
    });
    wireExpand(col);
  }

  function renderCounters(counters) {
    const col = $('#col-counters');
    if (!counters.length) { return; }
    col.innerHTML = '';
    counters.forEach((c) => {
      const el = document.createElement('div');
      el.className = 'aitem counter' + (c.suggested ? ' suggested' : '');
      el.innerHTML = `
        <div class="ai-head">
          <span class="ai-id">${c.counter_id}</span>
          <span class="ai-name">${esc(c.name)}</span>
          ${c.suggested ? `<span class="tag-suggested">${tx().suggested}</span>` : ''}
          <span class="ai-chev">+</span>
        </div>
        <div class="ai-why">${tx().addresses}: ${c.addresses.join(', ')}</div>
        <div class="ai-body">${esc(c.summary || '')}</div>`;
      col.appendChild(el);
    });
    wireExpand(col);
  }

  function renderCases(cases) {
    const box = $('#cases-list');
    if (!cases.length) { box.innerHTML = `<p class="empty-hint">${tx().noResults}</p>`; return; }
    box.innerHTML = '';
    cases.slice(0, 12).forEach((c) => {
      const meta = [c.year, c.country].filter(Boolean).join(' · ');
      const el = document.createElement('div');
      el.className = 'case-item';
      el.innerHTML = `
        <div class="ci-head"><span class="ci-name">${esc(c.name)}</span>${meta ? `<span class="ci-meta">${esc(meta)}</span>` : ''}</div>
        ${c.summary ? `<div class="ci-sum">${esc(c.summary)}</div>` : ''}
        ${c.read_more ? `<a class="ci-link" href="${esc(c.read_more)}" target="_blank" rel="noopener"><span data-ico="search"></span>${tx().readMore}</a>` : ''}`;
      box.appendChild(el);
    });
    hydrateIcons(box);
  }

  function renderResources(res) {
    const box = $('#resources');
    if (!res.length) { return; }
    box.innerHTML = '';
    res.forEach((r) => {
      const live = r.status === 'live';
      const el = document.createElement('a');
      el.className = 'tile ' + (live ? 'live' : 'soon');
      el.href = r.url; el.target = '_blank'; el.rel = 'noopener';
      el.innerHTML = `
        <span class="ti" data-ico="${r.kind === 'tool' ? 'flask' : 'book'}"></span>
        <h3>${esc(r.name)}</h3>
        <span class="dm">${r.owner === 'jlab' ? 'J-LAB' : 'EXTERNAL'}</span>
        <p>${esc(r.blurb)}</p>
        <div class="foot">
          <span class="st ${live ? 'on' : 'wait'}">${live ? 'LIVE' : 'SOON'}</span>
          <span class="go"><span data-ico="arrowUpRight"></span></span>
        </div>`;
      box.appendChild(el);
    });
    hydrateIcons(box);
  }

  /* ---------- export ---------- */
  function confirmedItems() {
    return (state.bundle?.techniques || [])
      .filter((t) => state.confirmed.has(t.technique_id))
      .map((t) => ({ technique_id: t.technique_id, rationale: t.rationale, confidence: t.confidence }));
  }
  function updateExport() {
    const has = state.confirmed.size > 0;
    $('#export-md').disabled = !has;
    $('#export-layer').disabled = !has;
  }
  async function doExport(kind) {
    const r = await fetch('/api/report', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lang: lang(), description: $('#case-input').value.trim(), techniques: confirmedItems() }),
    });
    const d = await r.json();
    if (kind === 'md') dl('disarm-analysis.md', d.markdown, 'text/markdown');
    else dl('disarm-layer.json', JSON.stringify(d.layer, null, 2), 'application/json');
  }
  function dl(name, content, mime) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([content], { type: mime }));
    a.download = name; a.click(); URL.revokeObjectURL(a.href);
  }

  /* ---------- framework browser ---------- */
  async function loadFramework() {
    const r = await fetch('/api/framework?lang=' + lang());
    state.framework = await r.json();
  }
  function renderFrameworkTree() {
    const fw = state.framework; const box = $('#framework-tree');
    if (!fw || !box) return;
    box.innerHTML = '';
    (fw.tree || []).forEach((phase) => {
      (phase.tactic_ids || []).forEach((taid) => {
        const tac = fw.tactics[taid] || {};
        const techIds = fw.tech_by_tactic[taid] || [];
        const el = document.createElement('div');
        el.className = 'aitem tactic';
        el.style.marginBottom = '8px';
        el.innerHTML = `
          <div class="ai-head"><span class="ai-id">${taid}</span><span class="ai-name">${esc(tac.name || '')}</span><span class="ai-chev">+</span></div>
          <div class="ai-why">${esc(phase.name)} · ${techIds.length} ${lang() === 'es' ? 'técnicas' : 'techniques'}</div>
          <div class="ai-body">${esc(tac.summary || '')}<br><br>${techIds.map((id) => `<span class="ai-id">${id}</span> ${esc((fw.techniques[id] || {}).name || '')}`).join('<br>')}</div>`;
        box.appendChild(el);
      });
    });
    wireExpand(box);
  }

  /* ---------- AI status pill ---------- */
  async function aiStatus() {
    try {
      const d = await (await fetch('/api/health')).json();
      const pill = $('#ai-status');
      if (d.ai_enabled) pill.innerHTML = `<span class="live-dot"></span>AI`;
      else pill.textContent = 'LOCAL';
    } catch (e) { /* ignore */ }
  }

  /* ---------- language change re-render ---------- */
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-set-lang]')) {
      setTimeout(async () => {
        await loadFramework();
        if (state.bundle) {
          // Re-fetch localized bundle would need the case; cheap path: re-render with what we have.
          renderAll();
        }
        renderFrameworkTree();
      }, 30);
    }
  });

  /* ---------- init ---------- */
  $('#analyze').addEventListener('click', analyze);
  $('#link-add').addEventListener('click', () => addLinkRow());
  $('#case-input').addEventListener('keydown', (e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') analyze(); });
  $('#export-md').addEventListener('click', () => doExport('md'));
  $('#export-layer').addEventListener('click', () => doExport('layer'));

  aiStatus();
  loadFramework().then(renderFrameworkTree);
  hydrateIcons();
})();
