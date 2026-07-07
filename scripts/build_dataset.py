#!/usr/bin/env python3
"""
build_dataset.py — DISARM v1.7 SQLite -> normalized JSON for the tagger.

Reads the master SQLite shipped in DISARMframeworks-17 and emits a single,
nested, app-ready dataset:

    data/disarm_en.json   English (DISARM original wording)
    data/disarm_es.json   Spanish (machine-translated stub by default;
                           --translate fills it with Claude, human review after)

The English content is DISARM Foundation's work (CC BY-SA 4.0). Our Spanish
translation is a derivative and is likewise released CC BY-SA 4.0. See NOTICE.

Usage:
    python scripts/build_dataset.py \
        --sqlite ../../DISARMframeworks-17/generated_files/DISARM_database.sqlite
    python scripts/build_dataset.py --translate     # also (re)build Spanish via Claude

The dataset is regenerable from upstream without touching the app code.
"""

import argparse
import json
import os
import sqlite3
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()  # so --translate can pick up ANTHROPIC_API_KEY from .env
except ImportError:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(ROOT, 'data')

DEFAULT_SQLITE = os.path.normpath(
    os.path.join(ROOT, '..', '..', 'DISARMframeworks-17',
                 'generated_files', 'DISARM_database.sqlite')
)

# DISARM version this dataset is generated from.
DISARM_VERSION = '1.7'
ATTRIBUTION = (
    'DISARM Frameworks v1.7 © DISARM Foundation, licensed CC BY-SA 4.0. '
    'Spanish translation © J-Lab, licensed CC BY-SA 4.0.'
)


def rows(conn, sql, params=()):
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def build_en(sqlite_path):
    """Read the SQLite and produce the nested English dataset."""
    if not os.path.exists(sqlite_path):
        sys.exit(f'ERROR: SQLite not found at {sqlite_path}\n'
                 'Pass --sqlite with the path to DISARM_database.sqlite.')

    conn = sqlite3.connect(sqlite_path)

    phases = rows(conn, 'SELECT disarm_id, name, rank, summary FROM phase ORDER BY rank')
    tactics = rows(conn, 'SELECT disarm_id, phase_id, name, rank, summary FROM tactic ORDER BY rank')
    techniques = rows(conn, 'SELECT disarm_id, tactic_id, name, summary FROM technique ORDER BY disarm_id')
    counters = rows(conn, 'SELECT disarm_id, tactic_id, metatechnique_id, name, summary FROM counter ORDER BY disarm_id')
    incidents = rows(conn, 'SELECT disarm_id, name, summary, year_started, attributions_seen, found_in_country FROM incident ORDER BY disarm_id')
    metatechniques = rows(conn, 'SELECT disarm_id, name, summary FROM metatechnique ORDER BY disarm_id')

    # Mapping tables.
    counter_technique = rows(conn, 'SELECT counter_id, technique_id FROM counter_technique')
    incident_technique = rows(conn, 'SELECT incident_id, technique_id, name FROM incident_technique')
    conn.close()

    # technique_id -> [counter ids]
    counters_for_tech = {}
    for m in counter_technique:
        counters_for_tech.setdefault(m['technique_id'], []).append(m['counter_id'])

    # technique_id -> [incident ids]
    incidents_for_tech = {}
    for m in incident_technique:
        incidents_for_tech.setdefault(m['technique_id'], set()).add(m['incident_id'])

    # Index everything by disarm_id for the flat lookups the app uses.
    phase_index = {p['disarm_id']: p for p in phases}
    tactic_index = {t['disarm_id']: t for t in tactics}
    counter_index = {c['disarm_id']: c for c in counters}
    incident_index = {i['disarm_id']: i for i in incidents}

    # Attach derived links onto each technique.
    for t in techniques:
        tid = t['disarm_id']
        t['counter_ids'] = sorted(set(counters_for_tech.get(tid, [])))
        t['incident_ids'] = sorted(incidents_for_tech.get(tid, []))

    # Group techniques under their tactic, tactics under their phase, for the
    # browser. Keep flat indexes too for O(1) lookups in the API.
    tech_by_tactic = {}
    for t in techniques:
        tech_by_tactic.setdefault(t['tactic_id'], []).append(t['disarm_id'])

    tactic_by_phase = {}
    for t in tactics:
        tactic_by_phase.setdefault(t['phase_id'], []).append(t['disarm_id'])

    tree = []
    for p in phases:
        p_node = {
            'disarm_id': p['disarm_id'],
            'name': p['name'],
            'tactic_ids': tactic_by_phase.get(p['disarm_id'], []),
        }
        tree.append(p_node)

    return {
        'meta': {
            'lang': 'en',
            'disarm_version': DISARM_VERSION,
            'attribution': ATTRIBUTION,
            'license': 'CC-BY-SA-4.0',
            'source': 'https://github.com/DISARMFoundation/DISARMframeworks-17',
            'counts': {
                'phases': len(phases),
                'tactics': len(tactics),
                'techniques': len(techniques),
                'counters': len(counters),
                'incidents': len(incidents),
                'metatechniques': len(metatechniques),
            },
        },
        'tree': tree,
        'phases': phase_index,
        'tactics': tactic_index,
        'techniques': {t['disarm_id']: t for t in techniques},
        'tech_by_tactic': tech_by_tactic,
        'counters': counter_index,
        'incidents': incident_index,
        'metatechniques': {m['disarm_id']: m for m in metatechniques},
    }


def build_es_stub(en):
    """Spanish dataset with the SAME structure/keys but untranslated text.

    By default we ship this stub so the app is bilingual-ready; --translate
    fills name/summary with Claude. IDs and relations are never translated.
    """
    es = json.loads(json.dumps(en))  # deep copy
    es['meta']['lang'] = 'es'
    es['meta']['translation_status'] = 'untranslated-stub'
    return es


def translate_es(en):
    """Translate name/summary of phases/tactics/techniques/counters via Claude.

    Requires ANTHROPIC_API_KEY. Translations are reviewed by a human before
    release (see README). IDs, ranks and relations are preserved verbatim.
    """
    try:
        import anthropic
    except ImportError:
        sys.exit('ERROR: --translate needs the anthropic SDK: pip install anthropic')

    key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not key:
        sys.exit('ERROR: --translate needs ANTHROPIC_API_KEY in the environment.')

    client = anthropic.Anthropic(api_key=key)
    model = os.environ.get('ANTHROPIC_MODEL', 'claude-haiku-4-5')
    es = build_es_stub(en)
    es['meta']['translation_status'] = 'machine-translated-unreviewed'

    def translate_batch(items):
        """items: list of {id, name, summary}. Returns id -> {name, summary}."""
        payload = json.dumps(items, ensure_ascii=False)
        msg = client.messages.create(
            model=model,
            max_tokens=4000,
            system=(
                'Eres traductor especializado en desinformación y operaciones de '
                'influencia. Traduce al español neutro (LatAm) los campos "name" y '
                '"summary" de cada objeto DISARM. NO traduzcas ni alteres los IDs. '
                'Conserva términos técnicos establecidos. Responde SOLO con un array '
                'JSON con la misma forma: [{"id","name","summary"}].'
            ),
            messages=[{'role': 'user', 'content': payload}],
        )
        text = ''.join(b.text for b in msg.content if b.type == 'text').strip()
        # Strip code fences if the model added them.
        if text.startswith('```'):
            text = text.split('```')[1].lstrip('json').strip()
        out = json.loads(text)
        return {o['id']: o for o in out}

    BATCH = 20
    for collection in ('phases', 'tactics', 'techniques', 'counters', 'metatechniques'):
        objs = list(en[collection].values())
        for start in range(0, len(objs), BATCH):
            chunk = objs[start:start + BATCH]
            items = [{'id': o['disarm_id'], 'name': o['name'], 'summary': o.get('summary', '')} for o in chunk]
            print(f'  translating {collection} {start + 1}-{start + len(chunk)}/{len(objs)}...')
            translated = translate_batch(items)
            for o in chunk:
                tr = translated.get(o['disarm_id'])
                if tr:
                    es[collection][o['disarm_id']]['name'] = tr['name']
                    es[collection][o['disarm_id']]['summary'] = tr['summary']
    return es


def write_review(en, es):
    """Emit a side-by-side EN/ES review file (CSV) for human term checking.

    Open data/translation_review.csv in any spreadsheet; edit the *_es columns,
    then run --apply-review to fold corrections back into data/disarm_es.json.
    """
    import csv
    path = os.path.join(DATA_DIR, 'translation_review.csv')
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['type', 'id', 'name_en', 'name_es', 'summary_en', 'summary_es'])
        for coll in ('phases', 'tactics', 'techniques', 'counters', 'metatechniques'):
            for oid, o in en[coll].items():
                e = es[coll].get(oid, {})
                w.writerow([coll, oid, o.get('name', ''), e.get('name', ''),
                            o.get('summary', ''), e.get('summary', '')])
    print(f'Wrote {path}  (edit *_es columns, then --apply-review)')


def apply_review(es):
    """Fold human-edited translation_review.csv back into the Spanish dataset."""
    import csv
    path = os.path.join(DATA_DIR, 'translation_review.csv')
    if not os.path.exists(path):
        sys.exit(f'ERROR: {path} not found. Run --review first.')
    n = 0
    with open(path, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            coll, oid = row['type'], row['id']
            if coll in es and oid in es[coll]:
                if row.get('name_es'):
                    es[coll][oid]['name'] = row['name_es']
                if row.get('summary_es'):
                    es[coll][oid]['summary'] = row['summary_es']
                n += 1
    es['meta']['translation_status'] = 'human-reviewed'
    print(f'Applied {n} reviewed rows from translation_review.csv')
    return es


def main():
    ap = argparse.ArgumentParser(description='Build DISARM tagger dataset from SQLite.')
    ap.add_argument('--sqlite', default=DEFAULT_SQLITE, help='Path to DISARM_database.sqlite')
    ap.add_argument('--translate', action='store_true', help='Build Spanish via Claude (needs ANTHROPIC_API_KEY)')
    ap.add_argument('--review', action='store_true', help='Also write data/translation_review.csv (EN vs ES side by side)')
    ap.add_argument('--apply-review', action='store_true', help='Fold edited translation_review.csv back into disarm_es.json')
    args = ap.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    en_path = os.path.join(DATA_DIR, 'disarm_en.json')
    es_path = os.path.join(DATA_DIR, 'disarm_es.json')

    # Apply-review path: edit the existing Spanish JSON in place, no rebuild.
    if args.apply_review:
        with open(en_path, encoding='utf-8') as f:
            en = json.load(f)
        with open(es_path, encoding='utf-8') as f:
            es = json.load(f)
        es = apply_review(es)
        with open(es_path, 'w', encoding='utf-8') as f:
            json.dump(es, f, ensure_ascii=False, indent=2)
        print(f'Wrote {es_path}')
        return

    print(f'Reading SQLite: {args.sqlite}')
    en = build_en(args.sqlite)
    print('Counts:', en['meta']['counts'])

    with open(en_path, 'w', encoding='utf-8') as f:
        json.dump(en, f, ensure_ascii=False, indent=2)
    print(f'Wrote {en_path}')

    if args.translate:
        print('Translating to Spanish via Claude...')
        es = translate_es(en)
    else:
        print('Spanish: writing untranslated stub (run with --translate to fill).')
        es = build_es_stub(en)

    with open(es_path, 'w', encoding='utf-8') as f:
        json.dump(es, f, ensure_ascii=False, indent=2)
    print(f'Wrote {es_path}')

    if args.review:
        write_review(en, es)


if __name__ == '__main__':
    main()
