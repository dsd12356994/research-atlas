"""Archive originals, extract all available text, and expose missing coverage explicitly."""
import hashlib
import io
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from pypdf import PdfReader
from .net import get
from .store import DATA, now, normalized_arxiv

SCHEMA=2

def atomic(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(path)

def archive(body, suffix):
    digest=hashlib.sha256(body).hexdigest()
    path=DATA/'originals'/digest[:2]/(digest+suffix)
    path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():
        tmp=path.with_suffix(suffix+'.tmp');tmp.write_bytes(body);tmp.replace(path)
    return {'path':str(path.relative_to(DATA)),'sha256':digest,'bytes':len(body)}

def paragraphs_from_html(body):
    soup=BeautifulSoup(body,'html.parser')
    article=soup.find('article')
    if article is None:
        raise ValueError('No article element')
    result=[];section='Introduction'
    for tag in article.find_all(['h1','h2','h3','h4','p','table']):
        if tag.find_parent('table'):
            continue
        value=' '.join(tag.get_text(' ',strip=True).split())
        if tag.name.startswith('h'):
            section=value
        elif value:
            result.append({'section':section,'text':value,'kind':'table_text' if tag.name=='table' else 'text'})
    if len(result)<5:
        raise ValueError('Insufficient article body')
    return result,{'format':'html','status':'text_extracted','paragraphs':len(result),
                   'tables_extracted':len(article.find_all('table')),
                   'limitations':['Figures are not interpreted; table structure and formulas require visual verification.']}

def paragraphs_from_pdf(body):
    if not body.lstrip().startswith(b'%PDF-'):
        raise ValueError('Expected PDF bytes, possibly a login/error page')
    pdf=PdfReader(io.BytesIO(body));result=[];blank=[];failed=[];section='PDF text'
    for index,page in enumerate(pdf.pages):
        try:
            text=page.extract_text() or ''
        except Exception as exc:
            failed.append({'page':index+1,'error':type(exc).__name__});continue
        if not text.strip():
            blank.append(index+1);continue
        # Headings are heuristic; page references remain authoritative provenance.
        buffer=[]
        def flush():
            if buffer:
                value=' '.join(' '.join(buffer).split())
                if value:
                    result.append({'section':section,'page':index+1,'text':value,'kind':'text'})
                buffer.clear()
        for line in text.splitlines():
            if len(line)<110 and re.match(r'^\s*(?:\d+(?:\.\d+)*\s*[.)]?\s*)?(?:abstract|introduction|conclusions?|limitations?|discussion|future work|references|appendix)\b',line,re.I):
                flush();section=line.strip()
            else:
                buffer.append(line)
        flush()
    if not result:
        raise ValueError('No extractable PDF text')
    return result,{'format':'pdf','status':'partial' if blank or failed else 'text_extracted',
        'total_pages':len(pdf.pages),'attempted_pages':len(pdf.pages),
        'pages_with_text':len(pdf.pages)-len(blank)-len(failed),
        'blank_or_image_pages':blank,'failed_pages':failed,
        'limitations':['No OCR or figure interpretation; PDF table/formula layout and headings may be imperfect.']}

def acquire(p, *, force=False):
    ident=json.dumps([p['id'],p.get('version'),p.get('updated'),SCHEMA],ensure_ascii=False)
    path=DATA/'fulltext'/(hashlib.sha256(ident.encode()).hexdigest()+'.json')
    if path.exists() and not force:
        cached=json.loads(path.read_text(encoding='utf-8'))
        # Failures are retryable; never permanently cache abstract-only fallback as fulltext.
        if cached['extraction']['status']=='text_extracted' and all(
            (DATA/a['path']).exists() for a in cached.get('originals',[])):
            return cached
    aid=normalized_arxiv(p.get('arxiv_id') or (p['id'] if p['id'].startswith('arxiv:') else ''))
    candidates=[]
    if aid:
        version=p.get('version') or aid
        if str(version).startswith('v'):
            version=aid+version
        candidates=[('html','https://arxiv.org/html/'+version),('pdf','https://arxiv.org/pdf/'+version)]
    else:
        # Publisher-provided links are candidates, not a promise of open access.
        candidates=[('pdf',url) for url in p.get('fulltext_candidates',[])[:3]
                    if urlparse(url).scheme=='https']
    originals=[];failures=[];pars=[];url=p['url']
    extraction={'format':'abstract','status':'unavailable','limitations':['Full text not obtained.']}
    for fmt,target in candidates:
        try:
            response=get(target,timeout=(15,60),max_bytes=64_000_000)
            original=archive(response.content,'.'+fmt)
            original.update(url=target,retrieved_at=now());originals.append(original)
            pars,extraction=(paragraphs_from_html(response.content) if fmt=='html' else paragraphs_from_pdf(response.content))
            url=target;break
        except Exception as exc:
            failures.append({'url':target,'error':type(exc).__name__})
    if not pars:
        pars=[{'section':'Abstract','text':p.get('abstract',''),'kind':'text'}]
    # Break long blocks without discarding tails; preserve section/page provenance.
    parts=[]
    for para in pars:
        for offset in range(0,len(para['text']),1800):
            parts.append(para|{'id':f'P{len(parts)+1:05d}','text':para['text'][offset:offset+1800]})
    digest=hashlib.sha256(json.dumps(parts,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    result={'schema':SCHEMA,'paper_id':p['id'],'url':url,'version':p.get('version'),
        'paper_updated':p.get('updated'),'retrieved_at':now(),'originals':originals,
        'failures':failures,'extraction':extraction,'paragraphs':parts,
        'extracted_paragraphs':len(parts),'extracted_characters':sum(len(x['text']) for x in parts),
        'content_sha256':digest,'read_level':'abstract' if extraction['format']=='abstract' else 'archived_text',
        'cache_path':str(path.relative_to(DATA))}
    atomic(path,result)
    return result

def chunks(source, char_budget=6000):
    if char_budget<1800:
        raise ValueError('Chunk budget must accommodate an extraction block')
    # Prioritize the sections most useful for gap analysis, but queue every remaining block.
    ordered=sorted(source['paragraphs'],key=lambda p:0 if re.search(
        r'limit|future|conclu|discussion',p['section'],re.I) else 1)
    groups=[];current=[];size=0
    for para in ordered:
        if current and size+len(para['text'])>char_budget:
            groups.append(current);current=[];size=0
        current.append(para);size+=len(para['text'])
    if current: groups.append(current)
    return [{'id':hashlib.sha256(json.dumps(g,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20],
             'paragraphs':g} for g in groups]
