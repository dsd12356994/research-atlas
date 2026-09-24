"""Persistent, transactional window/page harvesting with bounded work per run."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
from .store import now
from .acquisition import arxiv_page, crossref_page, InvalidPage

MINUTE=timedelta(minutes=1)

def dt(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)

def source_key(kind, query):
    # Query changes create a new backfill, never inherit a narrower query's watermark.
    # Version the enumeration semantics, including inclusive-minute date boundaries.
    versioned=('window-v2\n'+query) if kind=='crossref' else query
    return kind+':'+hashlib.sha256(versioned.encode()).hexdigest()[:16]

def save(db,key,state):
    db.execute('INSERT OR REPLACE INTO collector_state VALUES(?,?,?)',
               (key,json.dumps(state,ensure_ascii=False),now()))

def job(start,end):
    return {'start':start.isoformat(),'end':end.isoformat(),'offset':0,'cursor':None,
            'expected':None,'seen':[]}

def restart(item):
    item.update(offset=0,cursor=None,expected=None,seen=[])

def plan_to_cutoff(state, cfg, cutoff):
    """Extend a drained queue to this call's fixed cutoff, with normal overlap."""
    opts=cfg.get('harvest',{})
    previous=state.get('covered_until')
    start=(dt(previous)-timedelta(days=opts.get('overlap_days',2))) if previous else (
        cutoff-timedelta(days=cfg.get('lookback_days',7)))
    due=(cutoff-dt(state['last_reconciled_at'])).days>=opts.get('reconcile_every_days',7)
    if due:
        start=min(start,cutoff-timedelta(days=opts.get('backfill_days',30)))
    state['plan_is_reconciliation']=due
    state['plan_until']=cutoff.isoformat()
    state.setdefault('initial_since',start.isoformat())
    while start<=cutoff:
        end=min(cutoff,start+timedelta(days=1)-MINUTE)
        state['pending'].append(job(start,end));start=end+MINUTE

def collect(db, run_id, name, kind, query, cfg, ingest, *, clock=None, fetch=None):
    opts=cfg.get('harvest',{})
    cutoff=(clock or datetime.now(timezone.utc)).replace(second=0,microsecond=0)
    key=source_key(kind,query)
    row=db.execute('SELECT data FROM collector_state WHERE key=?',(key,)).fetchone()
    state=json.loads(row[0]) if row else {'name':name,'kind':kind,'query':query,
        'covered_until':None,'pending':[],'created_at':cutoff.isoformat(),
        'last_reconciled_at':cutoff.isoformat()}
    state['name']=name
    if not state['pending']:
        plan_to_cutoff(state,cfg,cutoff)
        with db:
            save(db,key,state)
    fetch=fetch or (arxiv_page if kind=='arxiv' else crossref_page)
    max_pages=opts.get('max_pages_per_source',20)
    size=opts.get('page_size',200) if kind=='arxiv' else opts.get('crossref_page_size',100)
    deadline=time.monotonic()+opts.get('seconds_per_source',90)
    completed=0;returned=0;new=0;pages=0;error=None
    while state['pending'] and pages<max_pages and time.monotonic()<deadline:
        item=state['pending'][0]
        # Remote cursors expire. Restart just that bounded window after a long pause.
        if kind=='crossref' and item.get('cursor') and (
            datetime.now(timezone.utc)-dt(item.get('requested_at',state['created_at']))).total_seconds()>120:
            restart(item)
        try:
            rows,meta=fetch(query,dt(item['start']),dt(item['end']),
                            offset=item['offset'],size=size,cursor=item.get('cursor'))
            pages+=1
            total=meta['total']
            if total>opts.get('max_window_results',5000) and item['offset']==0:
                lo,hi=dt(item['start']),dt(item['end'])
                if hi<=lo:
                    raise InvalidPage('Result cap reached within a one-minute window')
                mid=lo+MINUTE*int((hi-lo).total_seconds()//120)
                state['pending'][:1]=[job(lo,mid),job(mid+MINUTE,hi)]
                with db: save(db,key,state)
                continue
            if item['expected'] is not None and total!=item['expected']:
                restart(item)
                raise InvalidPage('Result set changed during paging; window will be replayed')
            ids=[p['id'] for p in rows]
            if len(ids)!=len(set(ids)) or set(ids).intersection(item['seen']):
                restart(item)
                raise InvalidPage('Duplicate page/identity; window will be replayed')
            if not rows and item['offset']<total:
                raise InvalidPage('Empty page before expected end')
            seen=item['seen']+ids
            terminal=meta['terminal']
            if terminal and len(seen)!=total:
                restart(item)
                raise InvalidPage('Terminal page count does not match unique identities')
            if not terminal and not rows:
                raise InvalidPage('Pagination made no progress')
            if kind=='crossref' and not terminal and not meta.get('next_cursor'):
                raise InvalidPage('Missing continuation cursor')
            # Metadata and checkpoint commit together. A crash can cause replay, never skip.
            with db:
                added=ingest(db,rows,cfg,commit=False)
                audit={'start':item['start'],'end':item['end'],'offset':item['offset'],
                       'total':total,'count':len(rows),'ids':ids,'terminal':terminal}
                db.execute('INSERT INTO collection_pages(run_id,source_key,data,at) VALUES(?,?,?,?)',
                    (run_id,key,json.dumps(audit),now()))
                item.update(offset=item['offset']+len(rows),cursor=meta.get('next_cursor'),
                            expected=total,seen=seen,requested_at=now())
                if terminal:
                    state['covered_until']=max(filter(None,[state.get('covered_until'),item['end']]))
                    state['pending'].pop(0);completed+=1
                if not state['pending']:
                    state['last_complete_at']=now()
                    if state.get('plan_is_reconciliation'):
                        state['last_reconciled_at']=state['plan_until']
                    # Resuming yesterday's queue must also plan today's interval.
                    # Keep the original page/time budget, and persist pending work
                    # even when this terminal page exhausts the remaining budget.
                    if dt(state['plan_until'])<cutoff:
                        plan_to_cutoff(state,cfg,cutoff)
                save(db,key,state)
            returned+=len(rows);new+=added
        except Exception as exc:
            error=type(exc).__name__+(': '+str(exc) if isinstance(exc,InvalidPage) else '')
            # Reload after transactional failure; retain an intentional replay reset.
            if not isinstance(exc,InvalidPage):
                persisted=db.execute('SELECT data FROM collector_state WHERE key=?',(key,)).fetchone()
                state=json.loads(persisted[0])
                if kind=='crossref' and state['pending']:
                    restart(state['pending'][0])
            state['last_error']=error
            with db: save(db,key,state)
            break
    pending=state['pending']; head=pending[0] if pending else {}
    state['last_status']='partial' if pending else 'complete'
    state['last_error']=error
    state['last_attempt_at']=now()
    with db: save(db,key,state)
    return dict(source_key=key,new=new,returned=returned,pages=pages,
        completed_windows=completed,pending_windows=len(pending),
        covered_until=state.get('covered_until'),plan_until=state['plan_until'],
        initial_since=state['initial_since'],truncated=bool(pending),
        next_offset=head.get('offset'),next_expected=head.get('expected'),
        error=error,stop_reason=error or ('budget_pending' if pending else 'enumerated'),
        coverage='declared query and update-date windows only; not global literature recall')
