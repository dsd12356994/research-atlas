from __future__ import annotations
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def normalized_arxiv(value):
    m = re.search(r'(\d{4}\.\d{4,5})(?:v\d+)?', value or '')
    if m:
        return m.group(1)
    m = re.search(r'([a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?', value or '')
    return m.group(1) if m else ''

def canonical_id(p):
    candidate=p.get('arxiv_id') or ''
    if not candidate:
        for value in (p.get('id',''),p.get('url','')):
            if value.startswith('arxiv:') or re.match(r'https?://(?:www\.)?arxiv.org/',value):
                candidate=value;break
    aid = normalized_arxiv(candidate)
    if aid:
        return 'arxiv:' + aid
    doi = re.sub(r'^https?://(?:dx\.)?doi.org/', '', p.get('doi') or '').lower().strip()
    if doi:
        return 'doi:' + doi
    # Do not merge records based solely on approximate title similarity.
    return p.get('id') or 'url:' + hashlib.sha256(p['url'].encode()).hexdigest()[:20]

def connect(path=None):
    DATA.mkdir(exist_ok=True)
    db = sqlite3.connect(path or DATA / 'research.db', timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS papers(id TEXT PRIMARY KEY, title TEXT, published TEXT,
      score REAL, data TEXT, first_seen TEXT, last_seen TEXT);
    CREATE TABLE IF NOT EXISTS versions(paper_id TEXT, signature TEXT, data TEXT,
      seen_at TEXT, PRIMARY KEY(paper_id,signature));
    CREATE TABLE IF NOT EXISTS reviews(paper_id TEXT PRIMARY KEY, data TEXT, reviewed_at TEXT);
    CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, started TEXT, finished TEXT,
      status TEXT, data TEXT);
    CREATE TABLE IF NOT EXISTS source_events(id INTEGER PRIMARY KEY, run_id TEXT,
      source TEXT, status TEXT, count INTEGER, data TEXT, at TEXT);
    CREATE TABLE IF NOT EXISTS model_calls(id INTEGER PRIMARY KEY, at TEXT, purpose TEXT,
      model TEXT, data TEXT);
    CREATE TABLE IF NOT EXISTS collector_state(key TEXT PRIMARY KEY, data TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS collection_pages(id INTEGER PRIMARY KEY, run_id TEXT,
      source_key TEXT, data TEXT, at TEXT);
    CREATE TABLE IF NOT EXISTS reading_chunks(paper_id TEXT, content_hash TEXT, chunk_id TEXT,
      data TEXT, at TEXT, PRIMARY KEY(paper_id,content_hash,chunk_id));
    CREATE TABLE IF NOT EXISTS reference_queue(doi TEXT PRIMARY KEY, parents TEXT,
      status TEXT, attempts INTEGER, error TEXT, updated_at TEXT);
    CREATE VIRTUAL TABLE IF NOT EXISTS paper_fts USING fts5(id UNINDEXED,title,abstract);
    ''')
    return db

def upsert(db, p, commit=True):
    p = dict(p)
    p['id'] = canonical_id(p)
    # Merge only explicit DOI equivalence, never title similarity.
    if p.get('doi'):
        same=db.execute("SELECT id FROM papers WHERE lower(json_extract(data,'$.doi'))=? LIMIT 1",
                        (p['doi'].lower(),)).fetchone()
        if same:
            p['id']=same['id']
    old = db.execute('SELECT data,first_seen FROM papers WHERE id=?', (p['id'],)).fetchone()
    stamp = now()
    first = old['first_seen'] if old else stamp
    if old:
        prev = json.loads(old['data'])
        # A delayed feed or an unversioned community listing must not roll metadata back.
        def version_number(item):
            m = re.search(r'v(\d+)$', item.get('version') or '')
            return int(m.group(1)) if m else 0
        older = (version_number(prev) > version_number(p) or
                 (prev.get('updated') and p.get('updated') and prev['updated'] > p['updated']))
        if older:
            for field in ('title','abstract','authors','published','updated','version'):
                if prev.get(field):
                    p[field] = prev[field]
        p['sources'] = sorted(set(prev.get('sources', []) + p.get('sources', [])))
        p['topics'] = sorted(set(prev.get('topics', []) + p.get('topics', [])))
        for k, v in prev.items():
            if not p.get(k):
                p[k] = v
        # Preserve curated read provenance separately from feed metadata.
        if prev.get('seed_audit'):
            p['seed_audit'] = prev['seed_audit']
    stable = {k:p.get(k) for k in ('title','abstract','authors','published','updated','version','doi')}
    sig = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    raw = json.dumps(p, ensure_ascii=False)
    db.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?,?)', (p['id'],sig,raw,stamp))
    db.execute('INSERT OR REPLACE INTO papers VALUES(?,?,?,?,?,?,?)',
               (p['id'],p['title'],p.get('published',''),p.get('score',0),raw,first,stamp))
    db.execute('DELETE FROM paper_fts WHERE id=?', (p['id'],))
    db.execute('INSERT INTO paper_fts VALUES(?,?,?)',(p['id'],p['title'],p.get('abstract','')))
    if commit:
        db.commit()
    return p['id'], old is None

def papers(db):
    return [json.loads(r['data']) | {'first_seen':r['first_seen']} for r in db.execute(
        'SELECT * FROM papers ORDER BY score DESC,published DESC')]

def review(db, pid, content):
    db.execute('INSERT OR REPLACE INTO reviews VALUES(?,?,?)',
               (pid,json.dumps(content,ensure_ascii=False),now()))
    db.commit()

def event(db, run, source, status, count=0, **details):
    db.execute('INSERT INTO source_events(run_id,source,status,count,data,at) VALUES(?,?,?,?,?,?)',
               (run,source,status,count,json.dumps(details,ensure_ascii=False),now()))
    db.commit()

def topic_score(p, config):
    text = (p.get('title','') + ' ' + p.get('abstract','')).lower()
    tags = [name for name, terms in config['topics'].items()
            if any(re.search(r'(?<!\w)' + re.escape(t) + r'(?!\w)',text) for t in terms)]
    score = sum({'Federated retrieval':10,'Multimodal RAG':9,'Query privacy':8,
                 'Retrieval + reasoning':4,'Beyond image + text':2,'Efficiency':2}[t] for t in tags)
    if 'Beyond image + text' in tags and 'Retrieval + reasoning' in tags:
        score += 5
    title=p.get('title','').lower()
    if re.search(r'\brag\b|retrieval|fedrag',title):
        score += 5
    if re.search(r'federat|multimodal|audio|video|sensor|privacy|private',title) and re.search(r'\brag\b|retrieval',title):
        score += 4
    return tags, score

def search(db, query, limit=12):
    terms = re.findall(r'[\w-]+',query)
    if not terms:
        return []
    fts = ' OR '.join('"'+t.replace('"','')+'"' for t in terms)
    rows = db.execute('SELECT papers.data FROM paper_fts JOIN papers ON papers.id=paper_fts.id '
                      'WHERE paper_fts MATCH ? ORDER BY bm25(paper_fts) LIMIT ?', (fts,limit))
    return [json.loads(r[0]) for r in rows]
