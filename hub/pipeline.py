from __future__ import annotations
import argparse
import json
import os
import random
import sys
import time
import uuid
from datetime import datetime,timezone,timedelta
from pathlib import Path
from .store import ROOT,DATA,connect,now,upsert,papers,review,event,topic_score,search
from . import sources,reader,harvest

def config():
    return json.loads((ROOT/'config.json').read_text(encoding='utf-8'))

def ingest(db,rows,cfg,commit=True):
    new=0
    for p in rows:
        p['topics'],p['score']=topic_score(p,cfg)
        _,added=upsert(db,p,commit=commit);new+=int(added)
    return new

def seeds(db,run,cfg):
    path=ROOT/'bootstrap'/'papers-reviewed.json'
    if not path.exists():
        return
    payload=json.loads(path.read_text(encoding='utf-8'))
    rows=payload['papers']
    try:
        verified={p['id']:p for p in sources.arxiv_ids([p['arxiv_id'] for p in rows if p.get('arxiv_id')])}
        event(db,run,'seed-arxiv-metadata','ok',len(verified))
    except Exception as exc:
        verified={};event(db,run,'seed-arxiv-metadata','unavailable',error=type(exc).__name__)
    for p in rows:
        base=dict(p);base.update(verified.get(p['id'],{}))
        base['published']=base.get('published') or p.get('published_date','')
        base['sources']=list(set(base.get('sources',[])+['reviewed-seed']))
        base['seed_audit']=p
        # Versions in the audit may be short v2; normalize before requesting a PDF.
        if str(base.get('version','')).startswith('v'):
            base['version']=base['arxiv_id']+base['version']
        ingest(db,[base],cfg)
        review(db,p['id'],dict(problem_zh=p['problem_zh'],method_zh=p['method_zh'],
            source_version=(p.get('arxiv_id','')+p.get('version','')) if str(p.get('version','')).startswith('v') else p.get('version'),
            read_level=p['read_level'],sections_read=p.get('sections_read',[]),
            claims=[dict(kind=l['kind'],statement_zh=l['text_zh'],source_url=l['evidence_url'],
                         section=l['section'],status='seed_section_review_agent_audited') for l in p['limitations']],
            evidence=p['evidence'],reviewed_at=now(),summary_status='seed_section_review_agent_audited'))

class RunLock:
    def __enter__(self):
        DATA.mkdir(exist_ok=True);self.file=open(DATA/'run.lock','a+b')
        if os.name=='nt':
            import msvcrt
            self.file.seek(0);self.file.write(b'0');self.file.flush();self.file.seek(0)
            msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return self
    def __exit__(self,*args):
        self.file.close()

def run(deep=None,bootstrap=False,offline=False,source_filter=None,page_budget=None,seconds=None):
    with RunLock():
        return _run(deep,bootstrap,offline,source_filter,page_budget,seconds)

def _run(deep,bootstrap,offline,source_filter=None,page_budget=None,seconds=None):
    db=connect();cfg=config();run_id=now()+'-'+uuid.uuid4().hex[:6]
    cfg.setdefault('harvest',{})
    if page_budget is not None: cfg['harvest']['max_pages_per_source']=page_budget
    if seconds is not None: cfg['harvest']['seconds_per_source']=seconds
    start=now();status='failed'
    counts={'new':0,'read':0,'failures':0,'truncated_sources':0,'pending_reads':0,
            'source_filter':source_filter,'model_call_budget':deep}
    db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',(run_id,start,None,'running','{}'));db.commit()
    try:
        if bootstrap:
            seeds(db,run_id,cfg)
        if not offline:
            tasks=[('arxiv-rss:'+c,lambda c=c:(sources.rss(c),{'coverage':'feed contents only'})) for c in cfg['rss_categories']]
            tasks+=[('huggingface-daily',lambda:(sources.huggingface(),{'coverage':'curated snapshot only; global total unknown; not a comprehensive source','limit':50}))]
            for name,task in tasks:
                if source_filter and not any(name.startswith(f) for f in source_filter): continue
                print('Collecting '+name,flush=True)
                try:
                    rows,details=task();counts['new']+=ingest(db,rows,cfg)
                    counts['truncated_sources']+=int(bool(details.get('truncated')))
                    event(db,run_id,name,'partial' if details.get('truncated') else 'ok',len(rows),**details)
                except Exception as exc:
                    counts['failures']+=1;event(db,run_id,name,'unavailable',error=type(exc).__name__)
            if cfg.get('references',{}).get('enabled') and (not source_filter or any('crossref-references'.startswith(f) for f in source_filter)):
                from .references import follow
                print('Following crossref-references',flush=True)
                try:
                    details=follow(db,cfg,ingest)
                    counts['new']+=details['new'];counts['failures']+=details['errors']
                    counts['reference_backlog']=details['pending_references']
                    event(db,run_id,'crossref-references','partial' if details['pending_references'] or details['errors'] else 'ok',details['returned'],**details)
                except Exception as exc:
                    counts['failures']+=1;event(db,run_id,'crossref-references','unavailable',error=type(exc).__name__)
            durable=[('arxiv-query:'+str(i+1),'arxiv',q) for i,q in enumerate(cfg['queries'])]
            durable += [(item['name'],'arxiv',item['query']) for item in cfg.get('broad_arxiv',[])]
            durable += [('crossref-query:'+str(i+1),'crossref',q) for i,q in enumerate(cfg.get('crossref_queries',[]))]
            for name,kind,query in durable:
                if source_filter and not any(name.startswith(f) for f in source_filter): continue
                print('Harvesting '+name,flush=True)
                try:
                    details=harvest.collect(db,run_id,name,kind,query,cfg,ingest)
                    counts['new']+=details['new']
                    counts['truncated_sources']+=int(details['truncated'])
                    counts['failures']+=int(bool(details['error']))
                    event(db,run_id,name,'partial' if details['truncated'] else 'ok',details['returned'],**details)
                    print(json.dumps({'source':name,**details},ensure_ascii=False),flush=True)
                except Exception as exc:
                    counts['failures']+=1;event(db,run_id,name,'unavailable',error=type(exc).__name__)
        max_reads=cfg['deep_reads_per_day'] if deep is None else deep
        # Daily budget includes successful reads across reruns; failures cannot loop indefinitely.
        today=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        already=db.execute("SELECT count(*) FROM model_calls WHERE purpose LIKE 'read:%' AND substr(datetime(at,'+8 hours'),1,10)=?",(today,)).fetchone()[0]
        max_reads=max(0,min(max_reads,cfg['deep_reads_per_day']-already))
        have={r[0]:json.loads(r[1]) for r in db.execute('SELECT paper_id,data FROM reviews')}
        last_no_text={r[0][7:]:r[1] for r in db.execute("SELECT source,max(at) FROM source_events WHERE source LIKE 'reader:%' AND json_extract(data,'$.error')='NoSourceText' GROUP BY source")}
        def needs_read(p):
            if p.get('demo'):return False
            if not (p.get('abstract') or p.get('arxiv_id') or p.get('fulltext_candidates')):
                return False
            failed=last_no_text.get(p['id'])
            if failed and datetime.now(timezone.utc)-datetime.fromisoformat(failed)<timedelta(days=7):
                return False
            previous=have.get(p['id'])
            if not previous:
                return True
            if previous.get('reading') and not previous['reading']['complete']:
                return True
            if previous.get('extraction',{}).get('status') in ('unavailable','partial'):
                return datetime.now(timezone.utc)-datetime.fromisoformat(previous['reviewed_at'])>=timedelta(days=7)
            return bool(p.get('version') and previous.get('source_version') and p['version']!=previous['source_version'])
        candidates=[p for p in papers(db) if needs_read(p)]
        # One daily exploration item outside the focused topic shortlist.
        focused=[p for p in candidates if p.get('score',0)>=4]
        general=[p for p in candidates if p.get('score',0)<4]
        exploration_used=db.execute("SELECT count(*) FROM source_events WHERE source LIKE 'reader:%' AND substr(datetime(at,'+8 hours'),1,10)=? AND json_extract(data,'$.exploration')=1",(today,)).fetchone()[0]
        explore=min(max(0,cfg.get('exploration_reads',1)-exploration_used),len(general),max(0,max_reads-1))
        selected=focused[:max_reads-explore]
        if explore:
            selected+=random.Random(today).sample(general,min(explore,len(general)))
        for p in selected[:max_reads]:
            print('Reading '+p['id']+' '+p['title'][:85],flush=True)
            try:
                source=sources.fulltext(p)
                if not source['paragraphs']:
                    counts['failures']+=1
                    event(db,run_id,'reader:'+p['id'],'unavailable',error='NoSourceText',retry_days=7)
                    continue
                result=reader.read_paper(db,p,source,max_chunks=1)
                result['source_version']=p.get('version')
                review(db,p['id'],result);counts['read']+=1
                pending=not result['reading']['complete'] or result['extraction']['status']!='text_extracted'
                counts['pending_reads']+=int(pending)
                event(db,run_id,'reader:'+p['id'],'partial' if pending else 'ok',len(result['claims']),read_level=result['read_level'],
                      rejected=len(result.get('rejected_claims',[])),reading=result['reading'],
                      extraction=result['extraction'],exploration=p.get('score',0)<4)
            except Exception as exc:
                counts['failures']+=1
                event(db,run_id,'reader:'+p['id'],'unavailable',error=type(exc).__name__)
        status='partial' if counts['failures'] or counts['truncated_sources'] or counts['pending_reads'] or counts.get('reference_backlog') else 'complete'
    except Exception as exc:
        counts['fatal_error']=type(exc).__name__;status='failed'
        raise
    finally:
        db.execute('UPDATE runs SET finished=?,status=?,data=? WHERE id=?',
                   (now(),status, json.dumps(counts),run_id));db.commit()
        from .export import export
        export(db)
        db.close()
    print(json.dumps({'status':status,**counts},ensure_ascii=False),flush=True)
    return 1 if status=='failed' else 2 if status=='partial' else 0

def main():
    p=argparse.ArgumentParser(description='Evidence-first literature agent')
    sub=p.add_subparsers(dest='cmd',required=True)
    r=sub.add_parser('run');r.add_argument('--deep',type=int);r.add_argument('--bootstrap',action='store_true');r.add_argument('--offline',action='store_true')
    r.add_argument('--source',action='append',help='Only source names with this prefix; recorded in run scope')
    r.add_argument('--pages-per-source',type=int);r.add_argument('--seconds-per-source',type=int)
    archive=sub.add_parser('archive');archive.add_argument('paper_id');archive.add_argument('--force',action='store_true')
    sub.add_parser('export')
    s=sub.add_parser('search');s.add_argument('query')
    a=sub.add_parser('ask');a.add_argument('question');a.add_argument('--keywords',required=True,help='English retrieval keywords, not a translated claim')
    sub.add_parser('model-check')
    args=p.parse_args()
    if args.cmd=='run':
        return run(args.deep,args.bootstrap,args.offline,args.source,args.pages_per_source,args.seconds_per_source)
    db=connect()
    if args.cmd=='archive':
        from .content import acquire
        row=db.execute('SELECT data FROM papers WHERE id=?',(args.paper_id,)).fetchone()
        if not row:
            print('Paper ID is not in the local library');db.close();return 1
        result=acquire(json.loads(row[0]),force=args.force)
        print(json.dumps({k:v for k,v in result.items() if k!='paragraphs'},ensure_ascii=False,indent=2))
    elif args.cmd=='export':
        from .export import export
        export(db)
    elif args.cmd=='search':
        for row in search(db,args.query):
            print(row['id'],row['title'],row['url'])
    elif args.cmd=='model-check':
        answer=reader.complete(db,'Reply with only the word OK.','health',16)
        print(json.dumps({'model':reader.model_config()['model'],'ok':answer.strip()=='OK'}))
    elif args.cmd=='ask':
        rows=search(db,args.keywords)
        if not rows:
            print('No matching local evidence; collect or broaden keywords first.');return 1
        answer=reader.ask(db,args.question,rows)
        # Validate citation identifiers; never present unresolved IDs as verified references.
        allowed={r['id'] for r in rows}
        import re
        cited=set(re.findall(r'arxiv:\d{4}\.\d{4,5}',answer))
        cited.update(re.findall(r'\[(doi:[^\]\s，,]+)',answer))
        validation={'cited_ids':sorted(cited),'allowed_ids':sorted(allowed),
                    'unresolved_ids':sorted(cited-allowed),'semantic_support':'not_independently_validated'}
        flags=[phrase for phrase in ('无人做过','目前无任何一篇','都是集中式','首次提出','整个领域没有') if phrase in answer]
        validation['overgeneralization_flags']=flags
        answer='[模型生成的研究草稿；引用身份检查不能证明论点受到原文支持]\n\n'+answer
        if flags:
            answer='[范围检查提醒：检测到可能超出当前证据集的概括，须回原文和补检索后修改]\n\n'+answer
        if cited-allowed:
            answer='[引用检查失败；以下仅为待审查模型草稿]\n'+answer
        target=ROOT/'reports'/'answers';target.mkdir(parents=True,exist_ok=True)
        path=target/(datetime.now().strftime('%Y%m%d-%H%M%S')+'.md')
        path.write_text('# '+args.question+'\n\n'+answer+'\n\n## 检索范围\n'+
            '\n'.join(f'- [{r["title"]}]({r["url"]})' for r in rows)+'\n',encoding='utf-8')
        path.with_suffix('.audit.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding='utf-8')
        print(answer);print('\nSaved: '+str(path))
    db.close()
    return 0

if __name__=='__main__':
    sys.exit(main())
