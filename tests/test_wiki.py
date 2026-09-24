import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from hub import wiki
from hub.store import connect,upsert,review

class WikiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.kb=patch.object(wiki,'KB',self.root/'knowledge');self.kb.start()
        self.db=connect(self.root/'test.sqlite')
        self.paper={'id':'arxiv:2609.12345','arxiv_id':'2609.12345','title':'A federated retrieval method',
            'abstract':'Privacy and communication.','url':'https://arxiv.org/abs/2609.12345',
            'topics':['Federated retrieval'],'sources':['test']}
        upsert(self.db,self.paper)
        review(self.db,self.paper['id'],{'problem_zh':'A problem','method_zh':'A bounded method',
            'claims':[{'kind':'author_limitation','statement_zh':'Limited evaluation','source_url':self.paper['url']}],
            'reading':{'chunks_done':1,'chunks_total':7},'source_version':'2609.12345v1'})
    def tearDown(self):
        self.db.close();self.kb.stop();self.tmp.cleanup()
    def test_bracketed_display_titles_preserve_links(self):
        path=wiki.KB/'wiki/notes/brackets.md'
        wiki.write_text(path,wiki.page_text({'id':'notes/brackets'},'[[sources/a|[DEMO] A title]] and [[sources/b|B [v2]]]'))
        self.assertEqual(wiki.load_page(path)['links'],['sources/a','sources/b'])

    def test_snapshot_is_immutable_and_detects_tampering(self):
        ref=wiki.immutable({'title':'original'});path=wiki.KB/ref
        first=path.read_bytes();self.assertEqual(wiki.immutable({'title':'original'}),ref)
        self.assertEqual(path.read_bytes(),first)
        path.write_text('{"title":"changed"}',encoding='utf-8')
        with self.assertRaises(ValueError):wiki.immutable({'title':'original'})
    def test_complete_json_fence_allowed_but_truncated_json_rejected(self):
        self.assertEqual(wiki.parse_completion('```json\n{"sections":[]}\n```'),{'sections':[]})
        with self.assertRaises(ValueError):wiki.parse_completion('```json\n{"sections":[]}')
        with self.assertRaises(ValueError):wiki.parse_completion('intro {"sections":[]} trailing')
    def test_answers_do_not_crowd_out_source_retrieval(self):
        wiki.compile_wiki(self.db)
        for i in range(10):
            wiki.write_text(wiki.KB/'wiki'/f'answers/{i}.md',wiki.page_text({'id':f'answers/{i}',
                'title':'federated','type':'answer'},'federated'))
        result=wiki.search_wiki('federated',source_only=True)
        self.assertTrue(result);self.assertTrue(all(p['type']!='answer' for p in result))
    def test_build_is_idempotent_and_manual_edits_survive(self):
        wiki.compile_wiki(self.db);key=wiki.source_id(self.paper['id']);path=wiki.KB/'wiki'/(key+'.md')
        original=path.read_text(encoding='utf-8');log=(wiki.KB/'wiki/log.md').read_text(encoding='utf-8')
        wiki.compile_wiki(self.db)
        self.assertEqual(log,(wiki.KB/'wiki/log.md').read_text(encoding='utf-8'))
        changed=original+'\nHuman annotation.\n';path.write_text(changed,encoding='utf-8')
        r=wiki.compile_wiki(self.db)
        self.assertEqual(path.read_text(encoding='utf-8'),changed)
        self.assertIn(key,r['health']['preserved_manual_edits'])
    def test_graph_has_real_endpoints_and_keeps_inference_distinct(self):
        result=wiki.compile_wiki(self.db);ids={n['id'] for n in result['graph']['nodes']}
        self.assertTrue(all(e['source'] in ids and e['target'] in ids for e in result['graph']['edges']))
        self.assertFalse(any(e['relation'] in ('supports','contradicts') for e in result['graph']['edges']))
        self.assertFalse(result['health']['issues'])
    def test_lint_finds_missing_link_and_stale_input(self):
        wiki.compile_wiki(self.db);key=wiki.source_id(self.paper['id'])
        wiki.write_text(wiki.KB/'wiki/answers/a.md',wiki.page_text({'id':'answers/a','title':'Test','type':'answer','input_hashes':{key:'old'}},'[[missing/page]]'))
        kinds={i['kind'] for i in wiki.lint()['issues']}
        self.assertIn('broken_link',kinds);self.assertIn('stale_input',kinds)
    def test_bad_model_citations_do_not_create_an_answer(self):
        wiki.compile_wiki(self.db)
        bad={'sections':[{'heading':'Result','items':[{'text':'Unsupported','pages':['made/up']}]}]}
        with patch('hub.reader.complete',return_value=json.dumps(bad)):
            with self.assertRaises(ValueError):wiki.compose(self.db,'Question','federated')
        self.assertFalse((wiki.KB/'wiki/answers').exists())
    def test_answer_is_persistent_with_input_fingerprints(self):
        wiki.compile_wiki(self.db);key=wiki.source_id(self.paper['id'])
        good={'sections':[{'heading':'Scope','items':[{'text':'Partial reading only','pages':[key]}]}]}
        with patch('hub.reader.complete',return_value=json.dumps(good)):
            result=wiki.compose(self.db,'Question','federated')
        page=wiki.load_page(wiki.KB/'wiki'/(result['page_id']+'.md'))
        self.assertIn(key,page['input_hashes']);self.assertEqual(page['status'],'model_synthesis_unreviewed')
        self.assertIn(key,page['links'])
    def test_source_reading_coverage_is_not_lost(self):
        result=wiki.compile_wiki(self.db)
        source=next(p for p in result['pages'] if p['type']=='source')
        self.assertIn('1/7',source['body']);self.assertEqual(source['reading']['chunks_done'],1)
    def test_user_note_does_not_become_verified_paper(self):
        result=wiki.add_note('My note','A hypothesis')
        page=wiki.load_page(wiki.KB/'wiki'/(result['page_id']+'.md'))
        self.assertEqual(page['type'],'note');self.assertEqual(page['sources'],[])
        self.assertEqual(page['status'],'user_note_unreviewed')

if __name__=='__main__':unittest.main()
