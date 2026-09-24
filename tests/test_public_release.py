import json
import os
import unittest
from unittest.mock import patch
from hub import reader, wiki

class PublicReleaseTests(unittest.TestCase):
    def test_explicit_environment_model_config(self):
        with patch.dict(os.environ,{'ATLAS_API_KEY':'test-placeholder','ATLAS_MODEL':'test-model','ATLAS_BASE_URL':'https://example.org/v1'},clear=True):
            self.assertEqual(reader.model_config()['model'],'test-model')

    def test_partial_environment_config_does_not_fall_back_silently(self):
        with patch.dict(os.environ,{'ATLAS_API_KEY':'test-placeholder'},clear=True):
            with self.assertRaises(RuntimeError):reader.model_config()

    def test_demo_is_not_research_evidence(self):
        with patch.object(wiki,'search_wiki',return_value=[{'type':'source','demo':True}]),patch('hub.reader.complete') as model:
            with self.assertRaises(ValueError):wiki.compose(None,'Question','demo')
            model.assert_not_called()

    def test_demo_links_have_no_real_paper_identity(self):
        payload=json.loads((wiki.ROOT/'examples/demo.json').read_text(encoding='utf-8'))
        for item in payload['papers']:
            self.assertTrue(item['paper']['id'].startswith('demo:'))
            self.assertTrue(item['paper']['url'].startswith('https://example.org/'))
            self.assertIn('DEMO',item['paper']['title'])

if __name__=='__main__':unittest.main()
