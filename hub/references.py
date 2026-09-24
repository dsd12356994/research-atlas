"""Bounded one-hop backward citation discovery from deposited Crossref references."""
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from .store import papers, now
from .net import get
from .acquisition import crossref_entry, InvalidPage

def follow(db,cfg,ingest):
    opts=cfg.get('references',{})
    eligible=[p for p in papers(db) if p.get('score',0)>=opts.get('min_score',8)
              and p.get('reference_dois') and any(s!='crossref-reference' for s in p.get('sources',[]))]
    parents=eligible[:opts.get('parent_limit',20)]
    candidates={}
    for p in parents:
        for doi in p['reference_dois']:
            if doi==p.get('doi'): continue
            candidates.setdefault(doi,set()).add(p['id'])
    with db:
        for doi,pids in candidates.items():
            row=db.execute('SELECT parents FROM reference_queue WHERE doi=?',(doi,)).fetchone()
            if row:
                pids.update(json.loads(row[0]))
                db.execute('UPDATE reference_queue SET parents=? WHERE doi=?',(json.dumps(sorted(pids)),doi))
            else:
                db.execute('INSERT INTO reference_queue VALUES(?,?,?,?,?,?)',
                    (doi,json.dumps(sorted(pids)),'pending',0,None,now()))
    cutoff=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    queue=db.execute("SELECT * FROM reference_queue WHERE status!='complete' AND (attempts=0 OR updated_at<?) ORDER BY attempts,doi LIMIT ?",
                     (cutoff,opts.get('max_requests_per_run',10))).fetchall()
    processed=0;new=0;errors=0;deadline=time.monotonic()+opts.get('seconds_per_run',60)
    for row in queue:
        if time.monotonic()>=deadline: break
        doi=row['doi']
        try:
            existing=db.execute("SELECT id FROM papers WHERE lower(json_extract(data,'$.doi'))=? OR id=?",(doi,'doi:'+doi)).fetchone()
            if not existing:
                payload=get('https://api.crossref.org/works/'+quote(doi,safe='')).json()
                if payload.get('status')!='ok': raise InvalidPage('Invalid reference lookup')
                p=crossref_entry(payload['message']);p['sources']=['crossref-reference']
                if p['doi']!=doi: raise InvalidPage('Reference DOI mismatch')
                with db:
                    new+=ingest(db,[p],cfg,commit=False)
                    db.execute("UPDATE reference_queue SET status='complete',attempts=attempts+1,error=NULL,updated_at=? WHERE doi=?",(now(),doi))
            else:
                with db:
                    db.execute("UPDATE reference_queue SET status='complete',error=NULL,updated_at=? WHERE doi=?",(now(),doi))
            processed+=1
        except Exception as exc:
            errors+=1
            with db:
                db.execute("UPDATE reference_queue SET status='pending',attempts=attempts+1,error=?,updated_at=? WHERE doi=?",(type(exc).__name__,now(),doi))
    pending=db.execute("SELECT count(*) FROM reference_queue WHERE status!='complete'").fetchone()[0]
    return {'returned':processed,'new':new,'errors':errors,'pending_references':pending,
            'parents_considered':len(parents),'eligible_parents':len(eligible),
            'scope':'One-hop backward DOI references from ranked parents; not forward citations or exhaustive citation coverage.'}
