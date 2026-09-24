"""Validated pages from independently indexed sources; no relevance filtering here."""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
import feedparser
from bs4 import BeautifulSoup
from .net import get
from .store import normalized_arxiv

class InvalidPage(ValueError):
    pass

def arxiv_entry(e, source='arxiv-api'):
    aid = normalized_arxiv(e.get('id',''))
    if not aid or not e.get('title'):
        raise InvalidPage('arXiv entry missing identity/title')
    raw_id = e.get('id','')
    version = re.search(re.escape(aid) + r'v\d+', raw_id)
    return dict(id='arxiv:'+aid, arxiv_id=aid, title=' '.join(e.title.split()),
        abstract=' '.join(e.get('summary','').split()), authors=[a.name for a in e.get('authors',[])],
        published=e.get('published',''), updated=e.get('updated',''),
        url='https://arxiv.org/abs/'+aid, version=version.group() if version else aid,
        doi=e.get('arxiv_doi',''), categories=[t.term for t in e.get('tags',[])], sources=[source])

def parse_atom(body):
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise InvalidPage('Incomplete or malformed XML') from exc
    if root.tag != '{http://www.w3.org/2005/Atom}feed':
        raise InvalidPage('Expected Atom feed')
    feed = feedparser.parse(body)
    if any('api/errors' in e.get('id','') for e in feed.entries):
        raise InvalidPage('arXiv API error entry')
    return feed

def arxiv_page(query, start, end, offset=0, size=200, cursor=None):
    expr=f'({query}) AND lastUpdatedDate:[{start:%Y%m%d%H%M} TO {end:%Y%m%d%H%M}]'
    response=get('https://export.arxiv.org/api/query', params=dict(
        search_query=expr, start=offset, max_results=min(size,2000),
        sortBy='lastUpdatedDate', sortOrder='ascending'))
    feed=parse_atom(response.content)
    try:
        total=int(feed.feed['opensearch_totalresults'])
        actual_start=int(feed.feed['opensearch_startindex'])
    except (KeyError, ValueError) as exc:
        raise InvalidPage('Missing pagination metadata') from exc
    if actual_start != offset or total < 0:
        raise InvalidPage('Unexpected pagination index/count')
    rows=[arxiv_entry(e) for e in feed.entries]
    return rows, dict(total=total, next_cursor=None, terminal=offset+len(rows)>=total,
                     query=expr, offset=offset, requested=size)

def crossref_entry(item):
    doi=(item.get('DOI') or '').lower().strip()
    title=' '.join(item.get('title') or [])
    if not doi or not title:
        raise InvalidPage('Crossref entry missing DOI/title')
    def date(field):
        value=item.get(field,{})
        if value.get('date-time'):
            return value['date-time']
        parts=(value.get('date-parts') or [[]])[0]
        return '-'.join(str(v).zfill(4 if i==0 else 2) for i,v in enumerate(parts))
    links=[l for l in item.get('link',[]) if l.get('content-type')=='application/pdf']
    return dict(id='doi:'+doi, doi=doi, title=title,
        abstract=BeautifulSoup(item.get('abstract',''),'html.parser').get_text(' ',strip=True),
        authors=[' '.join(filter(None,(a.get('given'),a.get('family')))) for a in item.get('author',[])],
        published=date('published'), updated=date('deposited'), url='https://doi.org/'+doi,
        venue='; '.join(item.get('container-title') or []), publication_type=item.get('type',''),
        sources=['crossref'], fulltext_candidates=[l['URL'] for l in links if l.get('URL')],
        reference_dois=sorted({r['DOI'].lower() for r in item.get('reference',[]) if r.get('DOI')}))

def crossref_page(query, start, end, offset=0, size=100, cursor=None):
    # Deposited dates include new/changed metadata, unlike publication-date-only polling.
    # Harvester windows are inclusive minutes; Crossref accepts seconds.
    inclusive_end=end+timedelta(seconds=59)
    params={'query.title':query, 'filter':f'from-update-date:{start:%Y-%m-%dT%H:%M:%S},until-update-date:{inclusive_end:%Y-%m-%dT%H:%M:%S}',
            'rows':min(size,1000), 'cursor':cursor or '*'}
    payload=get('https://api.crossref.org/works',params=params).json()
    if payload.get('status')!='ok' or not isinstance(payload.get('message'),dict):
        raise InvalidPage('Invalid Crossref envelope')
    msg=payload['message']
    if not isinstance(msg.get('items'),list) or not isinstance(msg.get('total-results'),int):
        raise InvalidPage('Missing Crossref items/count')
    rows=[crossref_entry(p) for p in msg['items']]
    return rows, dict(total=msg['total-results'],next_cursor=msg.get('next-cursor'),
        terminal=len(rows)<params['rows'],query=query,offset=offset,requested=params['rows'])
