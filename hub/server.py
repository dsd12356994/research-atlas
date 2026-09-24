"""Loopback-only UI bridge. No arbitrary file serving, shell, or credential endpoints."""
import argparse
import json
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlsplit
from .store import ROOT, connect

TOKEN=secrets.token_urlsafe(32)
JOBS={};MUTEX=threading.Lock();WORKER=ThreadPoolExecutor(max_workers=1)

def work(job_id,operation,payload):
    from .pipeline import RunLock
    from .wiki import compose,add_note,lint
    from .export import export
    try:
        with RunLock():
            db=connect()
            try:
                if operation=='ask':result=compose(db,payload['question'],payload['keywords'])
                elif operation=='note':result=add_note(payload['title'],payload['text'],payload.get('url',''))
                else:result=lint()
                export(db)
            finally:db.close()
        with MUTEX:JOBS[job_id]={'status':'complete','result':result}
    except Exception as exc:
        # Provider exceptions may include URLs; do not expose raw exceptions or secrets.
        message={'ValueError':'输入或模型引用校验未通过，本次没有保存 AI 草稿。',
                 'IncompleteCompletion':'模型输出未完整结束，本次没有保存草稿。',
                 'OSError':'采集任务可能正在使用工作台，或本地文件操作失败，请稍后再试。'}.get(type(exc).__name__,'操作未完成，请查看本地模型服务与网络状态后重试。')
        with MUTEX:JOBS[job_id]={'status':'failed','error':type(exc).__name__,'message':message}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,status,body,kind='application/json; charset=utf-8'):
        body=body.encode() if isinstance(body,str) else body
        self.send_response(status);self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.end_headers();self.wfile.write(body)
    def json(self,status,value):self.send(status,json.dumps(value,ensure_ascii=False))
    def valid_host(self):
        return self.headers.get('Host')=='127.0.0.1:'+str(self.server.server_port)
    def do_GET(self):
        if not self.valid_host():return self.json(403,{'error':'host'})
        path=urlsplit(self.path).path
        if path=='/api/health':return self.json(200,{'app':'research-atlas','version':2})
        if path=='/api/data':
            return self.send(200,(ROOT/'output'/'library.json').read_bytes())
        if path.startswith('/api/jobs/'):
            with MUTEX:value=JOBS.get(path.rsplit('/',1)[-1])
            return self.json(200 if value else 404,value or {'error':'not_found'})
        if path=='/' or path=='/index.html':
            html=(ROOT/'output'/'index.html').read_text(encoding='utf-8')
            html=html.replace('/*__BRIDGE__*/','window.ATLAS_BRIDGE='+json.dumps({'token':TOKEN})+';')
            return self.send(200,html,'text/html; charset=utf-8')
        allowed={'/assets/cytoscape.min.js':'text/javascript; charset=utf-8',
                 '/assets/app.js':'text/javascript; charset=utf-8',
                 '/assets/style.css':'text/css; charset=utf-8'}
        if path in allowed:return self.send(200,(ROOT/'output'/path.lstrip('/')).read_bytes(),allowed[path])
        return self.json(404,{'error':'not_found'})
    def do_POST(self):
        # Consume a bounded body before responding, including to rejected origins.
        # Closing a Windows TCP connection with unread request bytes can reset it
        # before the client receives the intended 403 response.
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<1 or size>150000:raise ValueError()
            self.connection.settimeout(5)
            raw=self.rfile.read(size)
            if len(raw)!=size:raise ValueError()
        except (ValueError,OSError):return self.json(400,{'error':'invalid_input'})
        origin='http://127.0.0.1:'+str(self.server.server_port)
        if not self.valid_host() or self.headers.get('Origin')!=origin or self.headers.get('X-Atlas-Token')!=TOKEN:
            return self.json(403,{'error':'origin_or_token'})
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.json(415,{'error':'content_type'})
        operation=urlsplit(self.path).path.removeprefix('/api/')
        if operation not in ('ask','note','lint'):return self.json(404,{'error':'not_found'})
        try:
            payload=json.loads(raw)
            required={'ask':{'question':2000,'keywords':500},'note':{'title':200,'text':40000},'lint':{}}[operation]
            if not isinstance(payload,dict):raise ValueError()
            for k,n in required.items():
                if not isinstance(payload.get(k),str) or not payload[k].strip() or len(payload[k])>n:raise ValueError()
            if 'url' in payload and (not isinstance(payload['url'],str) or len(payload['url'])>2000):raise ValueError()
        except (ValueError,TypeError):return self.json(400,{'error':'invalid_input'})
        with MUTEX:
            if any(j['status']=='running' for j in JOBS.values()):return self.json(409,{'message':'已有操作进行中，请等待完成。'})
            if len(JOBS)>30:
                for key in list(JOBS)[:-15]:JOBS.pop(key,None)
            job_id=uuid.uuid4().hex;JOBS[job_id]={'status':'running'}
        WORKER.submit(work,job_id,operation,payload)
        return self.json(202,{'job_id':job_id})

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8767);args=p.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    try:server.serve_forever()
    finally:server.server_close();WORKER.shutdown(wait=False)

if __name__=='__main__':main()
