import json
import tempfile
import unittest
from pathlib import Path
from hub.store import connect,canonical_id,upsert,papers,event,search
from hub.reader import validate_review

class Integrity(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.db=connect(Path(self.temp.name)/'test.db')
    def tearDown(self):
        self.db.close();self.temp.cleanup()
    def test_arxiv_versions_dedup_but_history_retained(self):
        base=dict(title='Federated Retrieval',url='https://arxiv.org/abs/2601.12345v1',id='arxiv:2601.12345v1',abstract='first',version='v1',sources=['arxiv'])
        upsert(self.db,base)
        upsert(self.db,base|{'id':'arxiv:2601.12345v2','abstract':'second','version':'v2','sources':['hf']})
        self.assertEqual(len(papers(self.db)),1)
        self.assertEqual(self.db.execute('select count(*) from versions').fetchone()[0],2)
        self.assertEqual(papers(self.db)[0]['sources'],['arxiv','hf'])
    def test_title_alone_does_not_merge_distinct_records(self):
        for i in (1,2):
            upsert(self.db,dict(id=f'doi:10.1234/{i}',title='A study',url=f'https://doi.org/10.1234/{i}'))
        self.assertEqual(len(papers(self.db)),2)
    def test_unavailable_not_empty_success(self):
        event(self.db,'run1','openalex','unavailable',error='HTTPError')
        self.assertEqual(self.db.execute('select status from source_events').fetchone()[0],'unavailable')
    def test_quote_must_match_correct_paragraph(self):
        source={'read_level':'abstract','url':'https://example.com','content_sha256':'abc',
                'paragraphs':[{'id':'P0001','section':'abstract','text':'We evaluate retrieval with a small query set.'}]}
        valid={'kind':'author_result','statement_zh':'小查询集','paragraph_id':'P0001','quote':'with a small query set'}
        forged=valid|{'quote':'better than all previous methods'}
        wrong=valid|{'paragraph_id':'P9999'}
        result=validate_review({'claims':[valid,forged,wrong]},source)
        self.assertEqual(len(result['claims']),1)
        self.assertEqual(len(result['rejected_claims']),2)
    def test_inference_stays_inference(self):
        source={'read_level':'pdf_excerpts','url':'https://example.com','content_sha256':'abc',
                'paragraphs':[{'id':'P1','section':'p2','text':'Our experiments focus on English speech.'}]}
        result=validate_review({'claims':[dict(kind='reviewer_inference',paragraph_id='P1',quote='focus on English speech',statement_zh='可能需要多语言验证')]},source)
        self.assertEqual(result['claims'][0]['kind'],'reviewer_inference')
        self.assertEqual(result['claims'][0]['status'],'quote_verified_semantics_unreviewed')
    def test_search_fts_special_characters(self):
        upsert(self.db,dict(id='a',title='Federated retrieval routing',url='https://example.com'))
        self.assertEqual(len(search(self.db,'"federated" OR routing:*')),1)

if __name__=='__main__':
    unittest.main()
