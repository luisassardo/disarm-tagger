# DISARM Tagger — `disarm.j-lab.tools`

A journalist-facing tool for mapping disinformation campaigns to the
[DISARM Framework](https://github.com/DISARMFoundation). Instead of ticking
boxes in an analyst matrix, a reporter **describes a campaign in plain language**
and an AI assistant (Claude tool-use) **suggests the DISARM techniques observed**,
restricted to valid framework IDs. The reporter confirms/edits and exports an
actionable, bilingual report.

Part of the J-Lab journalism toolkit. Companion bridge to C-LAB: the exported
Navigator layer is the interop format C-LAB would ingest into a threat-intel
platform.

## Why this and not the DISARM Navigator?

The official Navigator is an excellent analyst tool (a fork of MITRE ATT&CK
Navigator), but it's English-only and built around marking a matrix. This tool
keeps the DISARM taxonomy faithful (phases → tactics → techniques → counters,
canonical IDs) while changing the *workflow* to fit reporting: narrative in,
grounded techniques out, bilingual report and Navigator layer out.

## Stack

Flask + `anthropic` SDK, deployable on Render. Mirrors the J-Lab hosted-tool
pattern (server-side API key, graceful local fallback, ES/EN).

Frontend uses the **ARGUS / J-LAB design system** (Space Grotesk + IBM Plex
Mono, tactical-dark OKLCH palette, J-LAB amber-rose accent), vendored under
`static/argus/` (`desk.css` app shell + `node.js` i18n/clock + `icons.js`).
i18n is attribute-based (`data-en`/`data-es`) via `window.ArgusLang`.

### Layout

- **Row 1**: case description (with optional links) · similar documented cases
  (real DISARM incidents + a "read reports" search link each).
- **Row 2**: three columns — Techniques · Tactics · Counters. Counters are
  color-coded (green = suggested for this case).
- **Row 3**: recommended tools & resources (J-LAB tools + external orgs),
  ranked by the tactics involved.

Every item expands on click to show its full DISARM definition.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Build the dataset from the upstream DISARM SQLite (run once / on updates):
python scripts/build_dataset.py \
  --sqlite ../../DISARMframeworks-17/generated_files/DISARM_database.sqlite

# 2. (optional) Fill Spanish translations via Claude — needs ANTHROPIC_API_KEY:
python scripts/build_dataset.py --translate

# 3. Run:
cp .env.example .env   # add your ANTHROPIC_API_KEY (optional)
flask run              # or: gunicorn app:app
```

Without `ANTHROPIC_API_KEY` the app runs fine and uses the **local keyword
engine** for suggestions.

## API

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/` | GET | App UI (`?lang=es|en`) |
| `/api/health` | GET | `{ai_enabled, model, langs, disarm_version, ai_budget}` |
| `/api/framework?lang=` | GET | Full localized dataset for the browser |
| `/api/suggest` | POST | `{description, links[], lang}` → full bundle: `{techniques, tactics, counters, similar_cases, resources}` |
| `/api/report` | POST | `{title, description, techniques[], lang}` → `{markdown, layer}` |

## Data & licensing

- Framework content (`data/disarm_en.json`) is derived from **DISARM Frameworks
  v1.7, © DISARM Foundation, CC BY-SA 4.0**.
- The Spanish translation (`data/disarm_es.json`) is a J-Lab derivative, also
  **CC BY-SA 4.0**, with attribution.
- Application code is **MIT**.

See [`NOTICE`](NOTICE) for full attribution. The dataset is regenerable from
upstream via `scripts/build_dataset.py` without touching app code.

## Costs

Designed to run cheaply and predictably (self-maintained, ~$5/mo target). Layered,
cheapest-first:

1. **Anthropic console spend limit (hard backstop).** Set a monthly spend limit
   (e.g. $5) + email alert on the workspace/key at
   [console.anthropic.com](https://console.anthropic.com). This is the real
   ceiling: nothing below can exceed it.
2. **Cheap model + tight caps.** `claude-haiku-4-5`, `max_tokens=2000`,
   `AI_MAX_TOOL_TURNS=3`.
3. **Prompt caching.** The system prompt and tool schemas (the heavy static input)
   are cached, so repeated calls cost ~10% of the input.
4. **Daily budget.** `AI_DAILY_LIMIT` (default 50) analyses per UTC day; over it,
   the app silently degrades to the free local keyword engine. `/api/health`
   reports `ai_budget.remaining`.
5. **Per-IP rate limit.** 3/min and 10/hour on `/api/suggest` (HTTP 429 over that).
6. **Input cap.** `MAX_CASE_CHARS` (default 6000) bounds input tokens.

Web search for real report links is intentionally **not** wired in v0.1 (it adds
per-request cost); "similar cases" use search links instead.

## Deploy (Railway)

`railway.json` + `.python-version` are included; Railway builds via Nixpacks and
runs the `Procfile`/`startCommand` with gunicorn. Steps:

1. Create a Railway project from the repo.
2. Set env vars (dashboard): `ANTHROPIC_API_KEY` (secret), `ANTHROPIC_MODEL`,
   `AI_MAX_TOOL_TURNS`, `AI_DAILY_LIMIT`, `MAX_CASE_CHARS`.
3. Deploy; healthcheck is `/api/health`.
4. Add `disarm.j-lab.tools` as a custom domain, then a **CNAME** in Cloudflare to
   the Railway-provided domain.

`render.yaml` remains as an alternative host.

## Scope (v0.1)

In: AI-assisted tagging (ES/EN), framework browser, report + Navigator-layer
export, local fallback. Out (later): German, persistence/accounts, native STIX
export (when C-LAB has OpenCTI), full blue-framework mitigation guide.

> Tagged techniques are **leads for reporting, not verdicts or attribution.**
