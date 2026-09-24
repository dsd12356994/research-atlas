import http.client
import json
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from hub import server

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.port=self.http.server_port
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.jobs=dict(server.JOBS);server.JOBS.clear()
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join()
        server.JOBS.clear();server.JOBS.update(self.jobs)
    def request(self,method,path,body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        c.request(method,path,body=body,headers=headers or {})
        r=c.getresponse();result=(r.status,json.loads(r.read()));c.close();return result
    def trusted(self):
        return {'Origin':f'http://127.0.0.1:{self.port}','Content-Type':'application/json','X-Atlas-Token':server.TOKEN}
    def test_health_does_not_expose_credentials_or_token(self):
        status,body=self.request('GET','/api/health')
        self.assertEqual(status,200);self.assertEqual(set(body),{'app','version'})
    def test_untrusted_origin_and_host_are_rejected(self):
        headers=self.trusted()|{'Origin':'https://example.com'}
        self.assertEqual(self.request('POST','/api/ask','{}',headers)[0],403)
        self.assertEqual(self.request('GET','/api/health',headers={'Host':'evil.example'})[0],403)
    def test_files_outside_allowlist_are_not_served(self):
        for path in ['/runtime.local.json','/../runtime.local.json','/data/research.db','/knowledge/raw/test.json']:
            self.assertEqual(self.request('GET',path)[0],404)
    def test_missing_token_is_rejected(self):
        headers=self.trusted();headers.pop('X-Atlas-Token')
        self.assertEqual(self.request('POST','/api/lint','{}',headers)[0],403)
    def test_invalid_request_does_not_schedule_model(self):
        with patch.object(server.WORKER,'submit') as submit:
            status,_=self.request('POST','/api/ask',json.dumps({'question':'','keywords':'privacy'}),self.trusted())
            self.assertEqual(status,400);submit.assert_not_called()
    def test_one_job_at_a_time(self):
        with patch.object(server.WORKER,'submit') as submit:
            status,data=self.request('POST','/api/lint','{}',self.trusted())
            self.assertEqual(status,202);self.assertIn('job_id',data)
            self.assertEqual(self.request('POST','/api/lint','{}',self.trusted())[0],409)
            self.assertEqual(submit.call_count,1)

if __name__=='__main__':unittest.main()
