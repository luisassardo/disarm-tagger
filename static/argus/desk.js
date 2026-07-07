/* =============================================================
   ARGUS · DESK dashboard behavior
   View switching · signal radar · stat counters · chat demo
   · module spotlight · mobile rail. (i18n + clock from node.js)
   ============================================================= */
(function () {
  const RM = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const body = document.body;
  const deskId = body.getAttribute('data-desk') || 'desk';
  const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#5be3c3';

  /* ---------------- view switching ---------------- */
  const items = $$('.nav-item[data-view]');
  const views = $$('.view[data-view]');
  const crumbCur = $('.crumb .cur');
  function show(view) {
    views.forEach((v) => { v.hidden = v.getAttribute('data-view') !== view; });
    items.forEach((i) => i.classList.toggle('active', i.getAttribute('data-view') === view));
    const active = items.find((i) => i.getAttribute('data-view') === view);
    if (active && crumbCur) crumbCur.textContent = (active.querySelector('.nav-text') || active).textContent.trim();
    localStorage.setItem('argus_view_' + deskId, view);
    const vp = $('.viewport'); if (vp) vp.scrollTop = 0;
    body.classList.remove('nav-open');
  }
  items.forEach((i) => i.addEventListener('click', () => show(i.getAttribute('data-view'))));
  $$('[data-go]').forEach((b) => b.addEventListener('click', (e) => { e.preventDefault(); show(b.getAttribute('data-go')); }));
  const start = localStorage.getItem('argus_view_' + deskId) || 'hub';
  show(views.some((v) => v.getAttribute('data-view') === start) ? start : 'hub');

  // keep breadcrumb synced when language changes
  document.addEventListener('click', (e) => {
    if (e.target.closest('[data-set-lang]')) setTimeout(() => {
      const active = items.find((i) => i.classList.contains('active'));
      if (active && crumbCur) crumbCur.textContent = (active.querySelector('.nav-text') || active).textContent.trim();
    }, 0);
  });

  /* ---------------- mobile rail ---------------- */
  const toggle = $('.rail-toggle'); const scrim = $('.rail-scrim');
  if (toggle) toggle.addEventListener('click', () => body.classList.toggle('nav-open'));
  if (scrim) scrim.addEventListener('click', () => body.classList.remove('nav-open'));

  /* ---------------- module / tile spotlight ---------------- */
  $$('.module, .tile, .entry').forEach((el) => el.addEventListener('pointermove', (e) => {
    const r = el.getBoundingClientRect();
    el.style.setProperty('--mx', (e.clientX - r.left) + 'px');
    el.style.setProperty('--my', (e.clientY - r.top) + 'px');
  }));

  /* ---------------- stat counters ---------------- */
  function countUp(el) {
    const target = parseFloat(el.getAttribute('data-count'));
    if (isNaN(target) || RM) { return; }
    const dur = 900; const t0 = performance.now();
    const suffix = el.getAttribute('data-suffix') || '';
    const pad = el.getAttribute('data-pad') === '1';
    function step(t) {
      const k = Math.min(1, (t - t0) / dur);
      const v = Math.round(target * (1 - Math.pow(1 - k, 3)));
      el.textContent = (pad ? String(v).padStart(2, '0') : v) + suffix;
      if (k < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }
  const io = new IntersectionObserver((ents) => ents.forEach((en) => { if (en.isIntersecting) { countUp(en.target); io.unobserve(en.target); } }), { threshold: 0.4 });
  $$('[data-count]').forEach((el) => io.observe(el));

  /* ---------------- signal radar (mini) ---------------- */
  (function radar() {
    const svg = $('#signal-radar'); if (!svg) return;
    const NS = 'http://www.w3.org/2000/svg'; const S = 200, C = S / 2;
    const mid = getComputedStyle(document.documentElement).getPropertyValue('--mid').trim();
    const line = getComputedStyle(document.documentElement).getPropertyValue('--line').trim();
    const el = (t, a) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); return e; };
    svg.setAttribute('viewBox', `0 0 ${S} ${S}`);
    [0.96, 0.66, 0.36].forEach((s, i) => svg.appendChild(el('circle', { cx: C, cy: C, r: C * s, fill: 'none', stroke: i === 0 ? accent : line, 'stroke-width': 1, 'stroke-dasharray': i === 0 ? '0' : '2 5', opacity: i === 0 ? 0.6 : 1 })));
    svg.appendChild(el('line', { x1: C, y1: 6, x2: C, y2: S - 6, stroke: line, 'stroke-dasharray': '2 6' }));
    svg.appendChild(el('line', { x1: 6, y1: C, x2: S - 6, y2: C, stroke: line, 'stroke-dasharray': '2 6' }));
    for (let i = 0; i < 36; i++) { const a = i * 10 * Math.PI / 180, r1 = C * 0.96, r2 = r1 - (i % 9 === 0 ? 8 : 4); svg.appendChild(el('line', { x1: C + Math.cos(a) * r1, y1: C + Math.sin(a) * r1, x2: C + Math.cos(a) * r2, y2: C + Math.sin(a) * r2, stroke: i % 9 === 0 ? accent : mid, 'stroke-width': 1, opacity: i % 9 === 0 ? 0.7 : 0.35 })); }
    let cfg = (window.DESK_CONFIG && window.DESK_CONFIG.blips) || [{ a: 40, r: 0.4, s: -1, live: 1 }, { a: 150, r: 0.66, s: 0.8 }, { a: 250, r: 0.5, s: -1.2 }, { a: 320, r: 0.74, s: 0.6 }];
    const groups = cfg.map((b) => { const g = el('g', {}); const d = el('circle', { cx: 0, cy: 0, r: b.live ? 4 : 2.6, fill: accent, opacity: b.live ? 1 : 0.6 }); if (b.live) d.style.filter = `drop-shadow(0 0 5px ${accent})`; g.appendChild(d); if (b.live) { const p = el('circle', { cx: 0, cy: 0, r: 5, fill: 'none', stroke: accent, 'stroke-width': 1, opacity: 0.6 }); p.appendChild(el('animate', { attributeName: 'r', from: '5', to: '16', dur: '1.8s', repeatCount: 'indefinite' })); p.appendChild(el('animate', { attributeName: 'opacity', from: '0.6', to: '0', dur: '1.8s', repeatCount: 'indefinite' })); g.appendChild(p); } svg.appendChild(g); return { g, b }; });
    svg.appendChild(el('circle', { cx: C, cy: C, r: 2.6, fill: accent }));
    let t0 = performance.now();
    function frame(t) { const dt = (t - t0) / 1000; groups.forEach(({ g, b }) => { const a = (b.a + dt * b.s * 6) * Math.PI / 180; g.setAttribute('transform', `translate(${C + Math.cos(a) * C * b.r},${C + Math.sin(a) * C * b.r})`); }); requestAnimationFrame(frame); }
    if (!RM) requestAnimationFrame(frame); else groups.forEach(({ g, b }) => { const a = b.a * Math.PI / 180; g.setAttribute('transform', `translate(${C + Math.cos(a) * C * b.r},${C + Math.sin(a) * C * b.r})`); });
  })();

  /* ---------------- chat demo ---------------- */
  (function chat() {
    const form = $('#chat-form'); if (!form) return;
    const input = $('#chat-input'); const stream = $('#chat-stream');
    const reply = {
      en: "I'm a preview of the desk assistant, full responses arrive when the desk goes live. Meanwhile, explore Knowledge and Guides, or try the live tools.",
      es: 'Soy una vista previa del asistente del desk, las respuestas completas llegan cuando el desk esté activo. Mientras tanto, explora Conocimiento y Guías, o prueba las herramientas en vivo.',
    };
    const whoAI = $('#chat-aiwho') ? $('#chat-aiwho').textContent : 'Assistant';
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const txt = (input.value || '').trim(); if (!txt) return;
      const lang = (window.ArgusLang && window.ArgusLang.get()) || 'en';
      const me = document.createElement('div'); me.className = 'msg me';
      me.innerHTML = `<div class="av"><span data-ico="people"></span></div><div><div class="bub">${txt.replace(/</g, '&lt;')}</div></div>`;
      stream.appendChild(me); input.value = '';
      if (window.ArgusIcons) window.ArgusIcons.hydrate(me);
      const vp = $('.viewport'); if (vp) vp.scrollTop = vp.scrollHeight;
      setTimeout(() => {
        const ai = document.createElement('div'); ai.className = 'msg ai';
        ai.innerHTML = `<div class="av"><span data-ico="chat"></span></div><div><div class="who">${whoAI}</div><div class="bub"><p>${reply[lang] || reply.en}</p></div></div>`;
        stream.appendChild(ai);
        if (window.ArgusIcons) window.ArgusIcons.hydrate(ai);
        if (vp) vp.scrollTop = vp.scrollHeight;
      }, 650);
    });
  })();
})();
