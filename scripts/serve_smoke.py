"""Owned test server: shut down cleanly when the test driver's stdin closes."""
import sys
import threading
from http.server import ThreadingHTTPServer
from hub.server import Handler, WORKER

server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
print(server.server_port,flush=True)
try:sys.stdin.buffer.read()
finally:
    server.shutdown();server.server_close();WORKER.shutdown(wait=True)
