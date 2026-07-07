"""
DISARM Tagger API — backend for the journalist-facing DISARM tagger.
A J-Lab tool (disarm.j-lab.tools).

Respects the DISARM Frameworks (v1.7, CC BY-SA 4.0, © DISARM Foundation) and
evolves them for reporters: describe a disinformation campaign in plain language
and Claude suggests the DISARM techniques observed, restricted to valid IDs.
Degrades gracefully to a local keyword engine when no API key is set.

Deployable on Render (see render.yaml), Railway, or any Python host.
"""

from flask import Flask, jsonify, request, render_template, make_response
from flask_cors import CORS
import os
import json
import re
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()  # read .env in dev; harmless if absent (host sets real env vars)
except ImportError:
    pass

try:
    import anthropic
except ImportError:  # keep the app runnable without the SDK (falls back to local engine)
    anthropic = None

app = Flask(__name__)
CORS(app)

# Per-IP rate limit on the AI endpoint (defense against a single abuser draining
# the daily budget). In-memory storage is fine for a single Railway instance.
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    limiter = Limiter(get_remote_address, app=app, default_limits=[])
except ImportError:  # keep runnable without the dep
    limiter = None

def _limit(spec):
    """Decorator that applies a rate limit only when flask-limiter is present."""
    def wrap(fn):
        return limiter.limit(spec)(fn) if limiter else fn
    return wrap

# ==================== CONFIG ====================
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '').strip()
ANTHROPIC_MODEL = os.environ.get('ANTHROPIC_MODEL', 'claude-haiku-4-5')
MAX_TOOL_TURNS = int(os.environ.get('AI_MAX_TOOL_TURNS', '3'))
AI_ENABLED = bool(ANTHROPIC_API_KEY) and anthropic is not None

# --- Cost controls (see README "Costs"). The hard backstop is the Anthropic
# console monthly spend limit; these keep normal usage well under it. ---
AI_DAILY_LIMIT = int(os.environ.get('AI_DAILY_LIMIT', '50'))   # AI analyses per UTC day
MAX_CASE_CHARS = int(os.environ.get('MAX_CASE_CHARS', '6000'))  # cap input tokens
MAX_LINKS = 5

_anthropic_client = (
    anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if AI_ENABLED else None
)

# In-process daily budget counter. Resets each UTC day. In-memory only: a redeploy
# resets it, which is fine because the console spend limit is the real ceiling.
_budget = {'day': None, 'count': 0}


def _today():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def ai_budget_remaining():
    if _budget['day'] != _today():
        return AI_DAILY_LIMIT
    return max(0, AI_DAILY_LIMIT - _budget['count'])


def ai_budget_take():
    """Consume one unit of the daily AI budget. Returns True if allowed."""
    day = _today()
    if _budget['day'] != day:
        _budget['day'], _budget['count'] = day, 0
    if _budget['count'] >= AI_DAILY_LIMIT:
        return False
    _budget['count'] += 1
    return True


DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
SUPPORTED_LANGS = ('es', 'en')
DEFAULT_LANG = 'es'

# ==================== DATA ====================

def _load(lang):
    path = os.path.join(DATA_DIR, f'disarm_{lang}.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


DATASETS = {}
for _lang in SUPPORTED_LANGS:
    try:
        DATASETS[_lang] = _load(_lang)
    except FileNotFoundError:
        app.logger.warning('Dataset for %s missing — run scripts/build_dataset.py', _lang)

# English is the canonical set for ID validation / AI grounding (DISARM original).
EN = DATASETS.get('en') or next(iter(DATASETS.values()), None)
VALID_TECHNIQUE_IDS = set(EN['techniques'].keys()) if EN else set()


def resolve_lang():
    lang = (request.args.get('lang') or
            (request.get_json(silent=True) or {}).get('lang') or
            DEFAULT_LANG).lower()
    return lang if lang in DATASETS else DEFAULT_LANG


# ==================== UI STRINGS (ES/EN) ====================
STRINGS = {
    'es': {
        'badge_ai': 'IA · técnicas DISARM fundamentadas',
        'badge_local': 'modo local · coincidencia por palabras clave',
        'no_input': 'Describe la campaña que quieres analizar.',
    },
    'en': {
        'badge_ai': 'AI · grounded DISARM techniques',
        'badge_local': 'local mode · keyword match',
        'no_input': 'Describe the campaign you want to analyze.',
    },
}


# ==================== LOCAL FALLBACK ENGINE ====================

_WORD_RE = re.compile(r"[a-záéíóúñ0-9]+", re.IGNORECASE)
_STOP = set('the a an of to and or in on for with by from is are was were be been '
            'el la los las un una de a y o en con por para que se su sus del al es '
            'son fue era como más esta este'.split())


def _tokens(text):
    return {w for w in _WORD_RE.findall((text or '').lower()) if w not in _STOP and len(w) > 2}


def keyword_suggest(description, lang, limit=8):
    """Offline technique suggester: token overlap vs. technique name+summary.

    Scores against the ENGLISH text (richest source); returns localized objects.
    Output is a lead for reporting, never a verdict.
    """
    q = _tokens(description)
    if not q or not EN:
        return []
    scored = []
    for tid, t in EN['techniques'].items():
        doc = _tokens(t['name'] + ' ' + t.get('summary', ''))
        overlap = q & doc
        if overlap:
            scored.append((len(overlap), tid, sorted(overlap)))
    scored.sort(reverse=True)
    out = []
    for score, tid, matched in scored[:limit]:
        out.append({
            'technique_id': tid,
            'rationale': ('Coincidencia por palabras clave: ' if lang == 'es'
                          else 'Keyword match: ') + ', '.join(matched),
            'confidence': 'low',
        })
    return out


# ==================== AI (Claude tool-use) ====================

ANTHROPIC_TOOLS = [
    {
        'name': 'search_framework',
        'description': (
            'Search the DISARM technique catalogue by keyword to find candidate '
            'techniques. Returns matching techniques with their IDs, names and '
            'summaries. Use this to ground every suggestion in real DISARM IDs.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'query': {'type': 'string', 'description': 'Keywords describing a behaviour seen in the campaign (e.g. "fake accounts amplify").'},
                'limit': {'type': 'integer', 'description': 'Max results (default 12).'},
            },
            'required': ['query'],
        },
    },
    {
        'name': 'get_technique',
        'description': 'Get full details for one DISARM technique by its ID (e.g. "T0007"), including summary, related counters and incidents.',
        'input_schema': {
            'type': 'object',
            'properties': {'technique_id': {'type': 'string'}},
            'required': ['technique_id'],
        },
    },
    {
        'name': 'tag_techniques',
        'description': (
            'Submit the FINAL set of DISARM techniques observed in the campaign. '
            'Only use technique IDs that exist in the framework (verify with '
            'search_framework / get_technique first). Call this exactly once at the end.'
        ),
        'input_schema': {
            'type': 'object',
            'properties': {
                'techniques': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'technique_id': {'type': 'string', 'description': 'A valid DISARM technique ID, e.g. T0007.'},
                            'rationale': {'type': 'string', 'description': 'Why this technique fits, citing evidence from the description.'},
                            'confidence': {'type': 'string', 'enum': ['low', 'medium', 'high']},
                        },
                        'required': ['technique_id', 'rationale'],
                    },
                },
            },
            'required': ['techniques'],
        },
    },
]

SYSTEM_PROMPT = (
    "You are a disinformation-analysis assistant for journalists, built on the "
    "DISARM Framework (v1.7, © DISARM Foundation, CC BY-SA 4.0). The reporter "
    "describes a suspected influence operation; your job is to map it to the "
    "DISARM techniques that were OBSERVED.\n\n"
    "Rules:\n"
    "- Ground every technique in the framework: use search_framework and "
    "get_technique to find and confirm real technique IDs before tagging.\n"
    "- Never invent technique IDs. Only tag IDs that exist in the catalogue.\n"
    "- Tag only what the description supports as observable behaviour. Do not "
    "infer intent or attribution.\n"
    "- Reply in the user's language. Rationales must cite evidence from the "
    "description, not generic definitions.\n"
    "- These are leads for reporting, not verdicts. When done, call tag_techniques once."
)


def _localized_technique(tid, lang):
    """Merge the localized name/summary with the canonical EN relations."""
    base = EN['techniques'].get(tid)
    if not base:
        return None
    loc = DATASETS.get(lang, EN)['techniques'].get(tid, base)
    tac = DATASETS.get(lang, EN)['tactics'].get(base['tactic_id'], {})
    return {
        'technique_id': tid,
        'name': loc.get('name', base['name']),
        'summary': loc.get('summary', base.get('summary', '')),
        'tactic_id': base['tactic_id'],
        'tactic_name': tac.get('name', ''),
        'counter_ids': base.get('counter_ids', []),
        'incident_ids': base.get('incident_ids', []),
    }


def run_tool(name, tool_input, lang):
    """Execute a Claude tool call against the framework. Returns JSON-able data."""
    if name == 'search_framework':
        q = _tokens(tool_input.get('query', ''))
        limit = min(int(tool_input.get('limit', 12) or 12), 25)
        scored = []
        for tid, t in EN['techniques'].items():
            doc = _tokens(t['name'] + ' ' + t.get('summary', ''))
            overlap = q & doc
            if overlap:
                scored.append((len(overlap), tid))
        scored.sort(reverse=True)
        return [
            {'technique_id': tid, 'name': EN['techniques'][tid]['name'],
             'summary': EN['techniques'][tid].get('summary', '')[:300]}
            for _, tid in scored[:limit]
        ]
    if name == 'get_technique':
        tid = tool_input.get('technique_id', '')
        t = _localized_technique(tid, 'en')
        return t or {'error': f'No technique with id {tid}'}
    if name == 'tag_techniques':
        return {'received': True}
    return {'error': f'Unknown tool {name}'}


def _hydrate(suggestions, lang):
    """Validate IDs and attach localized technique detail for the frontend."""
    out = []
    seen = set()
    for s in suggestions:
        tid = (s.get('technique_id') or '').strip()
        if tid not in VALID_TECHNIQUE_IDS or tid in seen:
            continue
        seen.add(tid)
        detail = _localized_technique(tid, lang)
        if not detail:
            continue
        detail['rationale'] = s.get('rationale', '')
        detail['confidence'] = s.get('confidence', 'medium')
        out.append(detail)
    return out


# ==================== ANALYSIS BUNDLE (tactics / counters / cases / resources) ====================

def derive_tactics(tech_ids, lang):
    """Unique tactics covered by the detected techniques, in framework order."""
    ds = DATASETS.get(lang, EN)
    seen, out = set(), []
    # Order by the dataset's tactic order (tree/tactics dict insertion order).
    order = list(EN['tactics'].keys())
    by_tactic = {}
    for tid in tech_ids:
        base = EN['techniques'].get(tid)
        if base:
            by_tactic.setdefault(base['tactic_id'], []).append(tid)
    for taid in order:
        if taid in by_tactic and taid not in seen:
            seen.add(taid)
            tac = ds['tactics'].get(taid, EN['tactics'].get(taid, {}))
            phase = ds['phases'].get(tac.get('phase_id', ''), {})
            out.append({
                'tactic_id': taid,
                'name': tac.get('name', ''),
                'summary': tac.get('summary', ''),
                'phase_id': tac.get('phase_id', ''),
                'phase_name': phase.get('name', ''),
                'technique_ids': by_tactic[taid],
            })
    return out


def gather_counters(tech_ids, lang):
    """Counters mapped to the detected techniques (= 'suggested'), ranked by how
    many detected techniques each one addresses. These are mitigations a reporter
    can surface; color-coded as suggested in the UI."""
    ds = DATASETS.get(lang, EN)
    hits = {}
    for tid in tech_ids:
        for cid in EN['techniques'].get(tid, {}).get('counter_ids', []):
            hits.setdefault(cid, set()).add(tid)
    out = []
    for cid, addresses in sorted(hits.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        c = ds['counters'].get(cid, EN['counters'].get(cid, {}))
        out.append({
            'counter_id': cid,
            'name': c.get('name', ''),
            'summary': c.get('summary', ''),
            'addresses': sorted(addresses),
            'suggested': True,
        })
    return out


def _read_more_url(name):
    """Privacy-respecting search link so a reporter can read documented reports."""
    from urllib.parse import quote_plus
    return 'https://duckduckgo.com/?q=' + quote_plus(f'{name} disinformation campaign report')


def gather_similar_cases(tech_ids, lang):
    """Real documented influence operations from DISARM that used these techniques.
    Each carries a read-more search link; the AI path can enrich with article URLs."""
    ds = DATASETS.get(lang, EN)
    hits = {}
    for tid in tech_ids:
        for iid in EN['techniques'].get(tid, {}).get('incident_ids', []):
            hits.setdefault(iid, set()).add(tid)
    out = []
    for iid, matched in sorted(hits.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        i = ds['incidents'].get(iid, EN['incidents'].get(iid, {}))
        name = i.get('name', '')
        out.append({
            'incident_id': iid,
            'name': name,
            'summary': i.get('summary', ''),
            'year': i.get('year_started', ''),
            'country': i.get('found_in_country', ''),
            'matched': sorted(matched),
            'read_more': _read_more_url(name) if name else '',
        })
    return out


# Curated catalogue: J-Lab/ARGUS tools + external resources a journalist can reach
# for, mapped to the DISARM phases/tactics most relevant to each. Tactic IDs come
# from the framework (TA01..TA13). 'tactics': None => generally relevant.
RESOURCE_CATALOG = [
    # --- our tools (J-Lab / ARGUS family) ---
    {'kind': 'tool', 'owner': 'jlab', 'name': 'Social Monitor',
     'url': 'https://j-lab.tools', 'status': 'soon',
     'en': 'Track coordinated posting and amplification across social platforms.',
     'es': 'Rastrea publicación coordinada y amplificación en plataformas sociales.',
     'tactics': ['TA08', 'TA09', 'TA11']},
    {'kind': 'tool', 'owner': 'jlab', 'name': 'Verify',
     'url': 'https://j-lab.tools', 'status': 'soon',
     'en': 'Check provenance, reverse-search media and cross-reference claims.',
     'es': 'Verifica procedencia, busca medios al revés y contrasta afirmaciones.',
     'tactics': ['TA06', 'TA09']},
    {'kind': 'tool', 'owner': 'jlab', 'name': 'Meta Monetization Explorer',
     'url': 'https://monetizacion.j-lab.tools', 'status': 'live',
     'en': 'Find accounts monetizing on Meta, useful to trace funded amplifiers.',
     'es': 'Halla cuentas que monetizan en Meta, útil para rastrear amplificadores pagados.',
     'tactics': ['TA07', 'TA08']},
    {'kind': 'tool', 'owner': 'jlab', 'name': 'GovScan',
     'url': 'https://j-lab.tools', 'status': 'soon',
     'en': 'Probe official sites and records when state propaganda is involved.',
     'es': 'Examina sitios y registros oficiales cuando hay propaganda estatal.',
     'tactics': ['TA01', 'TA02']},
    # --- external resources / guides ---
    {'kind': 'resource', 'owner': 'ext', 'name': 'DISARM Navigator',
     'url': 'https://disarmframework.herokuapp.com/', 'status': 'live',
     'en': 'The official DISARM matrix, to cross-check the full technique tree.',
     'es': 'La matriz oficial DISARM, para contrastar el árbol completo de técnicas.',
     'tactics': None},
    {'kind': 'resource', 'owner': 'ext', 'name': 'EU DisinfoLab',
     'url': 'https://www.disinfo.eu/', 'status': 'live',
     'en': 'Investigations and methodology for disinformation cases.',
     'es': 'Investigaciones y metodología para casos de desinformación.',
     'tactics': None},
    {'kind': 'resource', 'owner': 'ext', 'name': 'DFRLab (Atlantic Council)',
     'url': 'https://dfrlab.org/', 'status': 'live',
     'en': 'Open-source research on influence operations and amplification.',
     'es': 'Investigación de fuentes abiertas sobre operaciones de influencia.',
     'tactics': ['TA08', 'TA09']},
    {'kind': 'resource', 'owner': 'ext', 'name': 'Bellingcat toolkit',
     'url': 'https://www.bellingcat.com/category/resources/', 'status': 'live',
     'en': 'OSINT tools and guides for verification and geolocation.',
     'es': 'Herramientas y guías OSINT para verificación y geolocalización.',
     'tactics': ['TA06', 'TA09']},
]


def pick_resources(tactic_ids, lang):
    """Rank the catalogue for this case: tactic-matched first, then general."""
    tset = set(tactic_ids)
    scored = []
    for r in RESOURCE_CATALOG:
        rt = r.get('tactics')
        score = 2 if (rt and tset & set(rt)) else (1 if rt is None else 0)
        if score:
            scored.append((score, r))
    scored.sort(key=lambda sr: -sr[0])
    out = []
    for _, r in scored:
        out.append({
            'kind': r['kind'], 'owner': r['owner'], 'name': r['name'],
            'url': r['url'], 'status': r['status'],
            'blurb': r.get(lang) or r.get('en', ''),
        })
    return out


def build_bundle(techniques, lang):
    """Assemble the full analysis bundle the frontend renders."""
    tech_ids = [t['technique_id'] for t in techniques]
    tactics = derive_tactics(tech_ids, lang)
    return {
        'techniques': techniques,
        'tactics': tactics,
        'counters': gather_counters(tech_ids, lang),
        'similar_cases': gather_similar_cases(tech_ids, lang),
        'resources': pick_resources([t['tactic_id'] for t in tactics], lang),
    }


# ==================== ROUTES ====================

@app.route('/')
def index():
    lang = resolve_lang()
    return render_template('index.html', lang=lang, ai_enabled=AI_ENABLED)


@app.route('/api/health')
def health():
    return jsonify({
        'ai_enabled': AI_ENABLED,
        'model': ANTHROPIC_MODEL if AI_ENABLED else None,
        'langs': list(DATASETS.keys()),
        'disarm_version': EN['meta']['disarm_version'] if EN else None,
        'ai_budget': {'daily_limit': AI_DAILY_LIMIT, 'remaining': ai_budget_remaining()} if AI_ENABLED else None,
    })


@app.route('/api/framework')
def framework():
    """Serve the full localized dataset for the framework browser."""
    lang = resolve_lang()
    return jsonify(DATASETS.get(lang, EN))


def _bundle_response(engine, badge, techniques, lang, **extra):
    payload = {'engine': engine, 'badge': badge}
    payload.update(build_bundle(techniques, lang))
    payload.update(extra)
    return jsonify(payload)


# Prompt-caching wrappers: the system prompt and the (large, static) tool schemas
# are the same on every call, so we mark them cacheable. Repeated calls within the
# ~5 min window reuse the cached prefix at ~10% of the input cost.
_CACHED_SYSTEM = [{'type': 'text', 'text': SYSTEM_PROMPT,
                   'cache_control': {'type': 'ephemeral'}}]
_CACHED_TOOLS = [dict(t) for t in ANTHROPIC_TOOLS]
_CACHED_TOOLS[-1] = {**_CACHED_TOOLS[-1], 'cache_control': {'type': 'ephemeral'}}


def _ai_tag(description, links, lang):
    """Run the Claude tool-use loop. Returns a list of tagged techniques or None."""
    case = description
    if links:
        case += '\n\nLinks / enlaces:\n' + '\n'.join(links)
    messages = [{'role': 'user', 'content': f'Idioma/Language: {lang}\n\nCaso / Case:\n{case}'}]
    tagged = None
    for _ in range(MAX_TOOL_TURNS):
        resp = _anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=2000,
            system=_CACHED_SYSTEM,
            tools=_CACHED_TOOLS,
            messages=messages,
        )
        if resp.stop_reason != 'tool_use':
            break
        messages.append({'role': 'assistant', 'content': resp.content})
        tool_results = []
        for b in resp.content:
            if b.type != 'tool_use':
                continue
            if b.name == 'tag_techniques':
                tagged = b.input.get('techniques', [])
            tool_results.append({
                'type': 'tool_result',
                'tool_use_id': b.id,
                'content': json.dumps(run_tool(b.name, b.input, lang), ensure_ascii=False),
            })
        messages.append({'role': 'user', 'content': tool_results})
        if tagged is not None:
            break
    return tagged


@app.route('/api/suggest', methods=['POST'])
@_limit('3 per minute')
@_limit('10 per hour')
def suggest():
    """Map a CASE description (plus optional links) to a full DISARM analysis
    bundle: techniques, tactics, suggested counters, similar documented cases and
    recommended tools/resources. Claude tool-use with a local keyword fallback."""
    body = request.get_json(silent=True) or {}
    description = (body.get('description') or '').strip()[:MAX_CASE_CHARS]
    links = [l.strip() for l in (body.get('links') or []) if l and l.strip()][:MAX_LINKS]
    lang = resolve_lang()
    if not description and not links:
        return jsonify({'error': STRINGS[lang]['no_input']}), 400

    # No key / SDK missing, or the daily AI budget is spent -> local engine (free).
    if not AI_ENABLED or not ai_budget_take():
        techs = _hydrate(keyword_suggest(description, lang), lang)
        note = None if AI_ENABLED else 'no_key'
        if AI_ENABLED and ai_budget_remaining() == 0:
            note = 'daily_cap'
        return _bundle_response('local', STRINGS[lang]['badge_local'], techs, lang,
                                **({'note': note} if note else {}))

    try:
        tagged = _ai_tag(description, links, lang)
        if tagged is None:
            techs = _hydrate(keyword_suggest(description, lang), lang)
            return _bundle_response('local-fallback', STRINGS[lang]['badge_local'], techs, lang, note='no_tag')
        techs = _hydrate(tagged, lang)
        return _bundle_response('claude', STRINGS[lang]['badge_ai'], techs, lang, model=ANTHROPIC_MODEL)
    except Exception as e:  # rate limits, API errors, anything
        app.logger.warning('suggest failed: %s', e)
        techs = _hydrate(keyword_suggest(description, lang), lang)
        note = 'rate_limited' if anthropic and isinstance(e, getattr(anthropic, 'RateLimitError', ())) else 'error'
        return _bundle_response('local-fallback', STRINGS[lang]['badge_local'], techs, lang, note=note)


@app.route('/api/report', methods=['POST'])
def report():
    """Build exportable artifacts from the journalist-confirmed technique IDs.

    Returns:
      - markdown: a human-readable report (ES/EN) with techniques, rationale,
        recommended counters, related incidents and DISARM attribution.
      - layer: a DISARM/ATT&CK-Navigator-compatible layer JSON (bridge to C-LAB).
    """
    body = request.get_json(silent=True) or {}
    lang = resolve_lang()
    title = (body.get('title') or ('Análisis DISARM' if lang == 'es' else 'DISARM analysis')).strip()
    description = (body.get('description') or '').strip()
    items = body.get('techniques') or []  # [{technique_id, rationale, confidence}]

    confirmed = []
    for it in items:
        tid = (it.get('technique_id') or '').strip()
        if tid not in VALID_TECHNIQUE_IDS:
            continue
        detail = _localized_technique(tid, lang)
        detail['rationale'] = it.get('rationale', '')
        detail['confidence'] = it.get('confidence', '')
        confirmed.append(detail)

    return jsonify({
        'markdown': _build_markdown(title, description, confirmed, lang),
        'layer': _build_navigator_layer(title, confirmed),
        'count': len(confirmed),
    })


def _build_markdown(title, description, techs, lang):
    is_es = lang == 'es'
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    L = {
        'gen': 'Generado' if is_es else 'Generated',
        'desc': 'Campaña analizada' if is_es else 'Campaign analyzed',
        'tech': 'Técnicas DISARM observadas' if is_es else 'DISARM techniques observed',
        'tactic': 'Táctica' if is_es else 'Tactic',
        'why': 'Por qué' if is_es else 'Rationale',
        'conf': 'Confianza' if is_es else 'Confidence',
        'counters': 'Contramedidas relacionadas' if is_es else 'Related counters',
        'incidents': 'Incidentes relacionados' if is_es else 'Related incidents',
        'none': '_Ninguna técnica confirmada._' if is_es else '_No techniques confirmed._',
        'note': ('Estas técnicas son pistas para la investigación periodística, no '
                 'un veredicto ni una atribución.' if is_es else
                 'These techniques are leads for reporting, not a verdict or attribution.'),
    }
    lines = [f'# {title}', '', f'*{L["gen"]}: {now} · DISARM v{EN["meta"]["disarm_version"]}*', '']
    if description:
        lines += [f'## {L["desc"]}', '', description, '']
    lines += [f'## {L["tech"]}', '']
    if not techs:
        lines += [L['none'], '']
    for t in techs:
        ds = DATASETS.get(lang, EN)
        lines.append(f'### {t["technique_id"]} — {t["name"]}')
        lines.append('')
        if t.get('tactic_name'):
            lines.append(f'- **{L["tactic"]}**: {t["tactic_id"]} {t["tactic_name"]}')
        if t.get('confidence'):
            lines.append(f'- **{L["conf"]}**: {t["confidence"]}')
        if t.get('rationale'):
            lines.append(f'- **{L["why"]}**: {t["rationale"]}')
        if t.get('counter_ids'):
            cs = ', '.join(f'{cid} {ds["counters"].get(cid, {}).get("name", "")}'.strip()
                           for cid in t['counter_ids'][:6])
            lines.append(f'- **{L["counters"]}**: {cs}')
        if t.get('incident_ids'):
            inc = ', '.join(f'{iid} {ds["incidents"].get(iid, {}).get("name", "")}'.strip()
                            for iid in t['incident_ids'][:6])
            lines.append(f'- **{L["incidents"]}**: {inc}')
        lines.append('')
    lines += ['---', '', f'> {L["note"]}', '', f'> {EN["meta"]["attribution"]}', '']
    return '\n'.join(lines)


def _build_navigator_layer(title, techs):
    """ATT&CK/DISARM Navigator 'layer' JSON — the interop bridge to C-LAB."""
    conf_score = {'low': 33, 'medium': 66, 'high': 100, '': 50}
    return {
        'name': title,
        'versions': {'layer': '4.5', 'navigator': '4.0',
                     'attack': f'DISARM-{EN["meta"]["disarm_version"]}'},
        'domain': 'disarm',
        'description': f'Generated by disarm.j-lab.tools — {EN["meta"]["attribution"]}',
        'techniques': [
            {
                'techniqueID': t['technique_id'],
                'tactic': t.get('tactic_name', ''),
                'score': conf_score.get(t.get('confidence', ''), 50),
                'comment': t.get('rationale', ''),
                'enabled': True,
            }
            for t in techs
        ],
        'gradient': {'colors': ['#ffe766', '#ff6666'], 'minValue': 0, 'maxValue': 100},
    }


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)
