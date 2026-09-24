from __future__ import annotations
from email.utils import parsedate_to_datetime
from datetime import datetime,timedelta,timezone
import feedparser
from bs4 import BeautifulSoup
from .net import get
from .store import normalized_arxiv
from .acquisition import arxiv_page, parse_atom, arxiv_entry, InvalidPage


def rss(category):
    # Use the same retry/throttle/body checks as the historical API, not a second client.
    import xml.etree.ElementTree as ET
    body=get('https://rss.arxiv.org/rss/'+category).content
    ET.fromstring(body)
    feed=feedparser.parse(body)
    if feed.bozo:
        raise InvalidPage('Malformed RSS')
    rows=[arxiv_entry(e,'arxiv-rss:'+category) for e in feed.entries]
    for p in rows:
        p['sources']=['arxiv-rss:'+category]
        p['id']='arxiv:'+normalized_arxiv(p['arxiv_id'])
        pub=str(p.get('published') or '')
        try:
            pub=parsedate_to_datetime(pub).isoformat()
        except (ValueError,TypeError):
            pass
        p['published']=pub
        p['abstract']=BeautifulSoup(p['abstract'],'html.parser').get_text(' ',strip=True)
    return rows

def arxiv_search(query, days=7, limit=50):
    # Compatibility helper. Production runs use the durable, time-sliced harvester.
    end=datetime.now(timezone.utc).replace(second=0,microsecond=0)
    start=end-timedelta(days=days)
    rows=[];seen=set();expected=None
    for _ in range(100):
        page,meta=arxiv_page(query,start,end,offset=len(rows),size=limit)
        if expected is not None and meta['total']!=expected:
            raise InvalidPage('Result count changed; retry a bounded time window')
        expected=meta['total']
        ids=[p['id'] for p in page]
        if len(set(ids))!=len(ids) or seen.intersection(ids):
            raise InvalidPage('Duplicate page')
        rows.extend(page);seen.update(ids)
        if meta['terminal'] and len(seen)==expected:
            return rows,dict(total_available=expected,returned=len(rows),truncated=False,window_days=days)
        if not page:
            raise InvalidPage('Empty page before expected end')
    return rows,dict(total_available=expected,returned=len(rows),truncated=True,window_days=days)

def arxiv_ids(ids):
    r=get('https://export.arxiv.org/api/query',params={'id_list':','.join(ids),'max_results':len(ids)})
    return [arxiv_entry(e,'arxiv-seed-verification') for e in parse_atom(r.content).entries]

def huggingface():
    payload=get('https://huggingface.co/api/daily_papers',params={'limit':50}).json()
    out=[]
    for row in payload:
        p=row['paper']; aid=normalized_arxiv(p['id'])
        if not aid:
            continue
        out.append(dict(id='arxiv:'+aid,arxiv_id=aid,title=p['title'],abstract=p.get('summary',''),
          authors=[a.get('name','') for a in p.get('authors',[])],published=p.get('publishedAt',''),
          url='https://arxiv.org/abs/'+aid,sources=['huggingface-daily'],hf_upvotes=p.get('upvotes',0)))
    return out

def fulltext(p):
    from .content import acquire
    return acquire(p)
