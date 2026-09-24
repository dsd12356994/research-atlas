import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import requests
from hub import acquisition, content, harvest, net, reader
from hub.pipeline import ingest
from hub.store import connect, papers, upsert, canonical_id

NOW=datetime(2026,9,23,12,tzinfo=timezone.utc)

def cfg(**changes):
    return {'topics':{},'lookback_days':0,'harvest':{
        'page_size':50,'max_pages_per_source':20,'seconds_per_source':60,**changes}}

def paper(n):
    return {'id':f'arxiv:2609.{n:05d}','arxiv_id':f'2609.{n:05d}',
            'title':f'Paper {n}','url':f'https://arxiv.org/abs/2609.{n:05d}',
            'abstract':'Evidence for a research question.','sources':['test']}

def page_of(total):
    def fetch(query,start,end,offset=0,size=50,cursor=None):
        rows=[paper(i) for i in range(offset,min(total,offset+size))]
        return rows,{'total':total,'terminal':offset+len(rows)>=total,'next_cursor':str(offset+len(rows))}
    return fetch

class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=connect(Path(self.tmp.name)/'test.db')
    def tearDown(self):
        self.db.close();self.tmp.cleanup()

class HarvestTests(DatabaseCase):
    def collect(self,**kwargs):
        return harvest.collect(self.db,'test-run','test','arxiv','test query',
            kwargs.pop('config',cfg()),kwargs.pop('ingest_fn',ingest),clock=NOW,**kwargs)
    def test_56_results_are_not_truncated_at_50(self):
        fetch=Mock(side_effect=page_of(56))
        result=self.collect(fetch=fetch)
        self.assertEqual([c.kwargs['offset'] for c in fetch.call_args_list],[0,50])
        self.assertEqual(len(papers(self.db)),56)
        self.assertFalse(result['truncated'])
    def test_budget_stop_then_resume_across_database_reopen(self):
        result=self.collect(fetch=page_of(56),config=cfg(max_pages_per_source=1))
        self.assertTrue(result['truncated']);self.assertIsNone(result['covered_until'])
        self.db.close();self.db=connect(Path(self.tmp.name)/'test.db')
        fetch=Mock(side_effect=page_of(56));result=self.collect(fetch=fetch)
        self.assertEqual(fetch.call_args.kwargs['offset'],50)
        self.assertEqual(len(papers(self.db)),56);self.assertFalse(result['truncated'])
    def test_failed_page_keeps_previous_checkpoint(self):
        count=0
        def fetch(*args,**kwargs):
            nonlocal count
            count+=1
            if count==2: raise requests.Timeout()
            return page_of(56)(*args,**kwargs)
        result=self.collect(fetch=fetch)
        self.assertEqual(result['next_offset'],50);self.assertTrue(result['error'])
        result=self.collect(fetch=page_of(56));self.assertFalse(result['truncated'])
    def test_resumed_old_queue_catches_up_in_same_call(self):
        self.collect(fetch=page_of(56),config=cfg(max_pages_per_source=1))
        tomorrow=NOW+timedelta(days=1)
        def fetch(query,start,end,**kwargs):
            if end==NOW:
                return page_of(56)(query,start,end,**kwargs)
            return [paper(100)],{'total':1,'terminal':True}
        result=harvest.collect(self.db,'resume','test','arxiv','test query',
            cfg(overlap_days=0),ingest,clock=tomorrow,fetch=fetch)
        self.assertFalse(result['truncated'])
        self.assertEqual(result['covered_until'],tomorrow.isoformat())
        self.assertEqual(len(papers(self.db)),57)
    def test_resumed_queue_budget_keeps_freshness_work_pending(self):
        self.collect(fetch=page_of(56),config=cfg(max_pages_per_source=1))
        tomorrow=NOW+timedelta(days=1)
        result=harvest.collect(self.db,'resume','test','arxiv','test query',
            cfg(max_pages_per_source=1,overlap_days=0),ingest,clock=tomorrow,fetch=page_of(56))
        self.assertEqual(result['pages'],1)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['covered_until'],NOW.isoformat())
        self.assertEqual(result['plan_until'],tomorrow.isoformat())
        state=json.loads(self.db.execute('SELECT data FROM collector_state').fetchone()[0])
        self.assertEqual(state['last_status'],'partial')
    def test_duplicate_page_is_never_complete(self):
        count=0
        def duplicate(*args,**kwargs):
            nonlocal count
            count+=1
            return [paper(0)],{'total':2,'terminal':count==2}
        result=self.collect(fetch=duplicate)
        self.assertTrue(result['truncated']);self.assertIn('Duplicate',result['error'])
        self.assertIsNone(result['covered_until'])
    def test_early_empty_page_is_not_success(self):
        result=self.collect(fetch=lambda *a,**k:([],{'total':56,'terminal':True}))
        self.assertTrue(result['truncated']);self.assertEqual(len(papers(self.db)),0)
    def test_changed_total_replays_window(self):
        count=0
        def fetch(*a,**k):
            nonlocal count
            count+=1
            return page_of(56 if count==1 else 57)(*a,**k)
        result=self.collect(fetch=fetch)
        self.assertIn('changed',result['error']);self.assertEqual(result['next_offset'],0)
    def test_ingest_and_checkpoint_are_one_transaction(self):
        def failing(db,rows,c,commit=True):
            upsert(db,rows[0],commit=False)
            raise RuntimeError('simulated disk failure')
        result=self.collect(fetch=page_of(2),ingest_fn=failing)
        self.assertEqual(len(papers(self.db)),0)
        self.assertEqual(result['next_offset'],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM collection_pages').fetchone()[0],0)
    def test_long_offline_gap_is_not_replaced_with_last_seven_days(self):
        key=harvest.source_key('arxiv','test query')
        old=NOW-timedelta(days=20)
        state={'name':'test','kind':'arxiv','query':'test query','covered_until':old.isoformat(),
               'pending':[],'created_at':old.isoformat(),'last_reconciled_at':NOW.isoformat()}
        with self.db: harvest.save(self.db,key,state)
        fetch=Mock(side_effect=page_of(0));self.collect(fetch=fetch,config=cfg(max_pages_per_source=1))
        self.assertLess(fetch.call_args.args[1],NOW-timedelta(days=19))
    def test_query_change_has_a_new_checkpoint(self):
        self.assertNotEqual(harvest.source_key('arxiv','retrieval'),harvest.source_key('arxiv','retrieval OR search'))
    def test_result_cap_splits_window_instead_of_dropping_records(self):
        c=cfg(max_pages_per_source=1,max_window_results=10);c['lookback_days']=1
        result=self.collect(fetch=page_of(56),config=c)
        state=json.loads(self.db.execute('SELECT data FROM collector_state').fetchone()[0])
        self.assertTrue(result['truncated']);self.assertGreater(len(state['pending']),1)
        self.assertTrue(all(x['offset']==0 for x in state['pending']))

class SourceAndTransportTests(unittest.TestCase):
    def test_broken_xml_is_rejected(self):
        with self.assertRaises(acquisition.InvalidPage):
            acquisition.parse_atom(b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>')
    def test_html_error_is_not_an_empty_atom_feed(self):
        with self.assertRaises(acquisition.InvalidPage): acquisition.parse_atom(b'<html/>')
    def test_missing_pagination_metadata_is_rejected(self):
        response=SimpleNamespace(content=b'<feed xmlns="http://www.w3.org/2005/Atom"/>')
        with patch.object(acquisition,'get',return_value=response):
            with self.assertRaises(acquisition.InvalidPage): acquisition.arxiv_page('q',NOW,NOW)
    def response(self,body=b'abc',status=200,headers=None):
        r=Mock(status_code=status,headers=headers or {},iter_content=lambda n:[body])
        return r
    def test_429_honors_retry_after(self):
        a=self.response(status=429,headers={'Retry-After':'4'});b=self.response()
        with patch.object(net.SESSION,'get',side_effect=[a,b]) as get,patch.object(net.time,'sleep') as sleep:
            result=net.get('https://example.com/a')
            self.assertEqual(get.call_count,2);self.assertEqual(result.content,b'abc') if isinstance(result,requests.Response) else self.assertEqual(result._content,b'abc')
            self.assertIn(unittest.mock.call(4.0),sleep.call_args_list)
    def test_body_length_mismatch_is_not_success(self):
        r=self.response(headers={'Content-Length':'100'})
        with patch.object(net.SESSION,'get',return_value=r),patch.object(net.time,'sleep'):
            with self.assertRaises(requests.exceptions.ChunkedEncodingError): net.get('https://example.com/b',retries=0)
    def test_download_budget_raises_instead_of_returning_prefix(self):
        with patch.object(net.SESSION,'get',return_value=self.response()),patch.object(net.time,'sleep'):
            with self.assertRaises(net.DownloadLimit): net.get('https://example.com/c',max_bytes=2)
    def test_long_retry_after_is_deferred_not_ignored(self):
        with patch.object(net.SESSION,'get',return_value=self.response(status=429,headers={'Retry-After':'3600'})),patch.object(net.time,'sleep'):
            with self.assertRaises(net.RetryDeferred): net.get('https://example.com/d')
    def test_doi_numbers_are_not_mistaken_for_arxiv_ids(self):
        self.assertEqual(canonical_id({'id':'doi:10.1/2609.12345','doi':'10.1/2609.12345'}),'doi:10.1/2609.12345')
    def test_crossref_includes_last_second_of_window(self):
        response=Mock();response.json.return_value={'status':'ok','message':{'items':[],'total-results':0}}
        with patch.object(acquisition,'get',return_value=response) as request:
            acquisition.crossref_page('retrieval',NOW,NOW)
        self.assertIn('until-update-date:2026-09-23T12:00:59',request.call_args.kwargs['params']['filter'])

class ContentTests(unittest.TestCase):
    def test_pdf_page_46_and_later_are_extracted(self):
        pdf=SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda i=i:f'Evidence from page {i+1}.') for i in range(50)])
        with patch.object(content,'PdfReader',return_value=pdf):
            pars,coverage=content.paragraphs_from_pdf(b'%PDF-test')
        self.assertEqual(coverage['attempted_pages'],50)
        self.assertEqual(pars[-1]['page'],50)
    def test_blank_page_is_reported(self):
        pdf=SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda:'First page'),SimpleNamespace(extract_text=lambda:'')])
        with patch.object(content,'PdfReader',return_value=pdf):
            _,coverage=content.paragraphs_from_pdf(b'%PDF-test')
        self.assertEqual(coverage['status'],'partial');self.assertEqual(coverage['blank_or_image_pages'],[2])
    def test_html_over_20000_chars_keeps_tail_and_short_paragraph(self):
        body=('<article><h1>Results</h1>'+('<p>'+'evidence '*1000+'</p>')*5+'<h2>Limitations</h2><p>A short limitation.</p><table><tr><td>42</td></tr></table></article>').encode()
        pars,_=content.paragraphs_from_html(body)
        self.assertGreater(sum(len(p['text']) for p in pars),20000)
        self.assertTrue(any(p['text']=='A short limitation.' for p in pars))
        self.assertTrue(any(p['kind']=='table_text' for p in pars))
    def test_chunking_covers_every_paragraph_exactly_once(self):
        pars=[{'id':str(i),'text':'a'*1800,'section':'Limitations' if i==19 else 'Methods'} for i in range(20)]
        work=content.chunks({'paragraphs':pars})
        ids=[p['id'] for c in work for p in c['paragraphs']]
        self.assertEqual(len(ids),20);self.assertEqual(set(ids),{str(i) for i in range(20)})
        self.assertEqual(work[0]['paragraphs'][0]['id'],'19')
    def test_archives_original_and_complete_extraction(self):
        body=('<article>'+''.join(f'<p>paragraph {i} '+('text '*1000)+'</p>' for i in range(8))+'</article>').encode()
        with tempfile.TemporaryDirectory() as directory,patch.object(content,'DATA',Path(directory)),patch.object(content,'get',return_value=SimpleNamespace(content=body)):
            result=content.acquire(paper(1))
            self.assertGreater(result['extracted_characters'],20000)
            original=Path(directory)/result['originals'][0]['path']
            self.assertEqual(original.read_bytes(),body)
            self.assertEqual(result['extraction']['status'],'text_extracted')

class ReadingTests(DatabaseCase):
    def source(self):
        return {'content_sha256':'abc','read_level':'archived_text','url':'https://example.com',
            'paragraphs':[{'id':f'P{i}','section':'Results','text':'evidence for research '*70} for i in range(12)],
            'extraction':{'status':'text_extracted'},'originals':[]}
    def answer(self,prompt,**kwargs):
        evidence=json.loads(prompt.split('以下JSON是待分析数据：\n')[1])['evidence']
        return json.dumps({'problem_zh':'problem','method_zh':'method','claims':[{
            'kind':'author_result','statement_zh':'result','paragraph_id':evidence[0]['id'],'quote':'evidence for research'}]})
    def test_reading_resumes_without_repeating_completed_chunks(self):
        with patch.object(reader,'complete',side_effect=lambda db,prompt,**kw:self.answer(prompt,**kw)) as complete:
            first=reader.read_paper(self.db,paper(1),self.source())
            second=reader.read_paper(self.db,paper(1),self.source())
        self.assertEqual(complete.call_count,2)
        self.assertEqual(first['reading']['chunks_done'],1);self.assertEqual(second['reading']['chunks_done'],2)
        self.assertFalse(second['reading']['complete'])
    def test_truncated_json_does_not_commit_read_progress(self):
        with patch.object(reader,'complete',return_value='{"problem_zh":"x","claims":[{"kind":"author_result"}'):
            with self.assertRaises(json.JSONDecodeError): reader.read_paper(self.db,paper(1),self.source())
        self.assertEqual(self.db.execute('SELECT count(*) FROM reading_chunks').fetchone()[0],0)
    def test_finish_reason_length_is_not_success(self):
        response=Mock();response.json.return_value={'choices':[{'message':{'content':'partial'},'finish_reason':'length'}],'usage':{}}
        with patch.object(reader,'model_config',return_value={'base_url':'https://example.com','api_key':'test','model':'test'}),patch.object(reader.requests,'post',return_value=response):
            with self.assertRaises(reader.IncompleteCompletion): reader.complete(self.db,'test','health')
        saved=json.loads(self.db.execute('SELECT data FROM model_calls').fetchone()[0])
        self.assertEqual(saved['status'],'incomplete');self.assertEqual(saved['finish_reason'],'length')
    def test_old_metadata_cannot_roll_back_version(self):
        upsert(self.db,paper(1)|{'version':'2609.00001v2','abstract':'new'})
        upsert(self.db,paper(1)|{'version':'2609.00001v1','abstract':'old'})
        self.assertEqual(papers(self.db)[0]['abstract'],'new')
    def test_doi_equivalence_merges_without_title_guessing(self):
        upsert(self.db,paper(1)|{'doi':'10.1234/test'})
        upsert(self.db,{'id':'doi:10.1234/test','doi':'10.1234/test','title':'Published version','url':'https://doi.org/10.1234/test'})
        self.assertEqual(len(papers(self.db)),1)

if __name__=='__main__': unittest.main()
