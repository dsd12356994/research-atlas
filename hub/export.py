from __future__ import annotations
import hashlib
import html
import json
import re
import sys
from datetime import datetime,timezone,timedelta
from .store import ROOT,now,papers

sys.path.insert(0,str(ROOT/'third_party'/'prior'/'src'))
from prior.models import Paper,Claim,Edge

def export(db):
    from .harvest import source_key
    cfg=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    specs=[('arxiv-query:'+str(i+1),'arxiv',q) for i,q in enumerate(cfg['queries'])]
    specs += [(x['name'],'arxiv',x['query']) for x in cfg.get('broad_arxiv',[])]
    specs += [('crossref-query:'+str(i+1),'crossref',q) for i,q in enumerate(cfg.get('crossref_queries',[]))]
    active_keys={source_key(kind,q) for _,kind,q in specs}
    rows=papers(db)
    reviews={r['paper_id']:json.loads(r['data']) for r in db.execute('SELECT * FROM reviews')}
    for p in rows:
        r=reviews.get(p['id'])
        if r:
            r['newer_version_pending']=bool(p.get('version') and r.get('source_version') and p['version']!=r['source_version'])
    runs=[dict(r)|{'data':json.loads(r['data'])} for r in db.execute('SELECT * FROM runs ORDER BY started DESC LIMIT 15')]
    events=[dict(r)|{'data':json.loads(r['data'])} for r in db.execute('SELECT * FROM source_events ORDER BY id DESC LIMIT 100')]
    collectors=[]
    for row in db.execute('SELECT * FROM collector_state ORDER BY key'):
        state=json.loads(row['data'])
        collectors.append({k:v for k,v in state.items() if k!='pending'}|{
            'key':row['key'],'active':row['key'] in active_keys,'updated_at':row['updated_at'],
            'pending_windows':len(state.get('pending',[])),
            'pending_preview':[{k:v for k,v in j.items() if k not in ('seen','cursor')}
                               for j in state.get('pending',[])[:3]]})
    existing={c['key'] for c in collectors}
    for name,kind,query in specs:
        key=source_key(kind,query)
        if key not in existing:
            collectors.append({'key':key,'active':True,'name':name,'kind':kind,'query':query,
                'last_status':'not_started','pending_windows':None,'pending_preview':[],
                'covered_until':None,'plan_until':None,'initial_since':None})
    extraction=[]
    for path in sorted((ROOT/'data'/'fulltext').glob('*.json')):
        record=json.loads(path.read_text(encoding='utf-8'))
        extraction.append({k:record.get(k) for k in ('paper_id','version','retrieved_at','originals',
            'extraction','extracted_paragraphs','extracted_characters','content_sha256','failures')})
    seed=ROOT/'data'/'seed.json'
    if not seed.exists():seed=ROOT/'bootstrap'/'papers-reviewed.json'
    audit=json.loads(seed.read_text(encoding='utf-8')) if seed.exists() else {}
    lock=ROOT/'sources.lock.json'
    tools=json.loads(lock.read_text(encoding='utf-8')) if lock.exists() else {}
    opportunities=audit.get('opportunities',[])
    graph={'nodes':[],'edges':[]};prior={'papers':[],'claims':[],'edges':[]}
    topics={}
    for p in rows:
        for t in p.get('topics',[]):
            topics[t]=topics.get(t,0)+1
        if p['id'] not in reviews:
            continue
        rev=reviews[p['id']]
        graph['nodes'].append({'id':p['id'],'type':'paper','label':p.get('seed_audit',{}).get('alias',p['title']),'url':p['url']})
        obj=Paper(id=p['id'],source='arxiv' if p['id'].startswith('arxiv:') else 'import',
            title=p['title'],abstract=p.get('abstract',''),url=p['url'],
            authors=p.get('authors',[]),doi=p.get('doi'),date=p.get('published','')[:10],
            date_source='source metadata',manifestations=[{'source':s} for s in p.get('sources',[])])
        prior['papers'].append(obj.to_dict())
        for i,c in enumerate(rev.get('claims',[])):
            cid=p['id']+'::c'+str(i+1)
            graph['nodes'].append({'id':cid,'type':c['kind'],'label':c['statement_zh'],
                                   'status':c['status'],'url':c.get('source_url',p['url'])})
            graph['edges'].append({'src':cid,'dst':p['id'],'relation':'stated_in' if c['kind']!='reviewer_inference' else 'inferred_from',
                                   'evidence':c.get('quote',''),'section':c.get('section','')})
            # Schema compatibility, not an assertion that Prior's extraction or inference ran.
            prior['claims'].append(Claim(id=cid,paper_id=p['id'],text=c['statement_zh'],
                  claim_type='background',evidence=c.get('quote',''),location=c.get('section',''),confidence=0.0).to_dict())
            prior['edges'].append(Edge(src=cid,dst=p['id'],relation='stated_in',
                  evidence=c.get('quote',''),confidence=0.0,source='text',level='meta').to_dict())
    for o in opportunities:
        graph['nodes'].append({'id':'opportunity:'+o['id'],'type':'unconfirmed_opportunity','label':o['title_zh']})
        for pid in o.get('supporting_papers',[]):
            graph['edges'].append({'src':'opportunity:'+o['id'],'dst':pid,
                'relation':'motivated_by_reviewer','evidence':o.get('next_check_zh','')})
    for t in topics:
        graph['nodes'].append({'id':'topic:'+t,'type':'topic_rule','label':t})
    for p in rows:
        if p['id'] in reviews:
            for t in p.get('topics',[]):
                graph['edges'].append({'src':p['id'],'dst':'topic:'+t,'relation':'keyword_classification',
                    'evidence':'Deterministic title/abstract keywords; not an inferred scientific relation.'})
    from .wiki import compile_wiki
    knowledge=compile_wiki(db)
    bundle={'generated_at':now(),'papers':rows,'reviews':reviews,'runs':runs,'events':events,
            'knowledge':knowledge,
            'collectors':collectors,'extraction':extraction,
            'topics':topics,'graph':graph,'opportunities':opportunities,'tools':tools,
            'demo':any(p.get('demo') for p in rows),'settings':{'daily_read_limit':cfg['deep_reads_per_day']},
            'audit':{k:v for k,v in audit.items() if k!='papers'}}
    out=ROOT/'output';out.mkdir(exist_ok=True)
    import shutil
    for asset in (ROOT/'web'/'assets').glob('*'):
        if asset.is_file():
            (out/'assets').mkdir(exist_ok=True)
            shutil.copy2(asset,out/'assets'/asset.name)
    for name,value in [('library.json',bundle),('graph.json',graph),('prior-compatible.json',prior)]:
        atomic(out/name,json.dumps(value,ensure_ascii=False,indent=2))
    atomic(out/'knowledge-graph.json',json.dumps(knowledge['graph'],ensure_ascii=False,indent=2))
    atomic(out/'knowledge.json',json.dumps(knowledge,ensure_ascii=False,indent=2))
    atomic(out/'coverage.json',json.dumps({'generated_at':now(),'collectors':collectors,
        'extraction':extraction,'claim':'Only configured sources, queries, and declared date ranges are audited.'},ensure_ascii=False,indent=2))
    # Escape HTML closing tags, external titles must never become executable markup.
    script=json.dumps(bundle,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    template=(ROOT/'web'/'index.html').read_text(encoding='utf-8')
    atomic(out/'index.html',template.replace('/*__DATA__*/',script))
    vault=ROOT/'vault';(vault/'papers').mkdir(parents=True,exist_ok=True)
    for p in rows:
        if p['id'] not in reviews:
            continue
        r=reviews[p['id']];slug=p['id'].replace(':','-').replace('/','-')
        lines=['# '+p['title'],'',f'- Source: {p["url"]}',f'- Read level: {r["read_level"]}',
          f'- Status: {r["summary_status"]}','', '## 具体问题',r.get('problem_zh',''),'',
          '## 方法与条件',r.get('method_zh',''),'', '## 证据与局限']
        if r.get('reading'):
            progress=r['reading']
            lines[4:4]=[f'- Reading chunks: {progress["chunks_done"]}/{progress["chunks_total"]}; complete extracted text: {progress["complete"]}',
                         '- Extraction: '+json.dumps(r.get('extraction',{}),ensure_ascii=False)]
        for c in r.get('claims',[]):
            lines+=['',f'### {c["kind"]}',c['statement_zh'],
              f'来源：[{c.get("section","source")}]({c.get("source_url",p["url"])})',
              '状态：'+c['status']]
            if c.get('quote'):
                lines+=['> '+c['quote']]
        for e in r.get('evidence',[]):
            lines+=['',f'> {e.get("quote_en","")}',f'[{e.get("section","source")}]({e["url"]})']
        lines+=['','相关主题：'+' '.join(f'[[{t}]]' for t in p.get('topics',[])),
                '', '未进行代码复现；未来工作不等于当前研究空白。']
        atomic(vault/'papers'/(slug+'.md'),'\n'.join(lines)+'\n')
    for t in topics:
        links=[f'- [[papers/{p["id"].replace(":","-").replace("/","-")}|{p["title"]}]]'
               for p in rows if t in p.get('topics',[]) and p['id'] in reviews]
        atomic(vault/(t.replace('/','-')+'.md'),'# '+t+'\n\n'+'\n'.join(links)+'\n')
    atomic(vault/'START.md','# Research Hub\n\n打开同目录作为 Obsidian vault。\n\n'+
           '\n'.join(f'- [[{t}]]' for t in topics)+'\n')
    today=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    report=ROOT/'reports';report.mkdir(exist_ok=True)
    recent=[p for p in rows if p.get('first_seen','')[:10]==today]
    lines=['# Research Hub 日报 · '+today,'',f'库中 {len(rows)} 篇；已有阅读卡 {len(reviews)} 篇。',
      '范围：arXiv CS / stat.ML 广域增量与重点查询、Crossref 标题检索、RSS/HF 社区快照。完成状态仅针对声明的查询和更新时间窗口。',
      '未完成代码复现、系统性新颖性证明；原文存在校验与语义支持校验不同。','', '## 重点阅读队列']
    lines += [f'- [{p["title"]}]({p["url"]}) · {", ".join(p.get("topics",[]))} · '+
        ('已有阅读卡' if p['id'] in reviews else '仅元数据/摘要，待阅读') for p in (recent or rows)[:30]]
    lines+=['','## 本轮可读的证据卡（不是复现结果）']
    read_today=[p for p in rows if p['id'] in reviews and reviews[p['id']].get('reviewed_at','')[:10]==today]
    for p in read_today[:8]:
        r=reviews[p['id']]
        lines+=['',f'### [{p["title"]}]({p["url"]})',
                '问题：'+r.get('problem_zh',''),'方法：'+r.get('method_zh',''),
                '阅读范围：'+r['read_level']]
        lines += [f'- {c["kind"]}：{c["statement_zh"]}（{c.get("section","")}；待人工复核）'
                  for c in r.get('claims',[]) if c['kind'] in ('author_limitation','author_future_work','reviewer_inference')]
    lines+=['','## 最新采集状态']
    if runs:
        lines+=[f'- {e["source"]}: {e["status"]}, {e["count"]} records; {json.dumps(e["data"],ensure_ascii=False)}'
                 for e in events if e['run_id']==runs[0]['id']]
    lines+=['','## 持久化覆盖与待补区间']
    for c in collectors:
        if not c['active']:
            continue
        lines += [f'- {c["name"]}: 连续采集位置 {c.get("covered_until")}; 待补 {c["pending_windows"]} 个窗口; 最近错误 {c.get("last_error")}']
    lines+=['','## 原文与阅读覆盖',
            f'- 已建立 {len(extraction)} 份版本化原文提取清单；原文件在 data/originals，完整可提取文本在 data/fulltext。',
            '- 旧阅读卡未记录逐块覆盖，不能当作读完全文；新卡显示已完成/总块数。无 OCR、图像理解或语义正确性保证。']
    lines+=['','## 如何转成研究问题',
            '从问题卡挑一个场景，先确认最近邻论文和其复现条件，再写可证伪的假设。换模态本身不构成贡献。']
    atomic(report/(today+'.md'),'\n'.join(lines)+'\n')
    future=['# 作者限制与未来工作队列','','持续从阅读卡导出。作者提出方向的时间可能早于目前进展，必须补查后续论文；此处不判定新颖性。']
    for p in rows:
        r=reviews.get(p['id'])
        if not r:
            continue
        cs=[c for c in r.get('claims',[]) if c['kind'] in ('author_limitation','author_future_work')]
        if cs:
            future+=['',f'## [{p["title"]}]({p["url"]})','阅读范围：'+r['read_level']]
            future+=[f'- {c["statement_zh"]} — [{c.get("section","原文")}]({c.get("source_url",p["url"])})；{c["status"]}' for c in cs]
    atomic(report/'future-work-queue.md','\n'.join(future)+'\n')
    bib=[]
    def clean(s):
        return str(s).replace('\\','').replace('{','').replace('}','').replace('%','\\%').replace('&','\\&')
    for p in rows:
        if p['id'] not in reviews:
            continue
        key=p['id'].replace(':','').replace('.','')
        bib+=['@misc{'+key+',','  title={'+clean(p['title'])+'},',
               '  author={'+clean(' and '.join(p.get('authors',[])))+'},',
               '  year={'+clean(p.get('published','')[:4])+'},',
               '  url={'+p['url']+'}','}']
    atomic(out/'reviewed.bib','\n'.join(bib)+'\n')

def atomic(path,text):
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(text,encoding='utf-8');tmp.replace(path)
