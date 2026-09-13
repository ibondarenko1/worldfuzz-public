"""Authenticated loopback HTTP and a UI over the shared task service."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit, unquote

from .contracts import OPERATIONS, Fault, envelope, openapi, strict_object


def make_server(service, port=0, token=None, docs_base_url=None):
    gateway=service if hasattr(service,'authenticate') else None
    token=token or secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def setup(self):
            super().setup();self.connection.settimeout(5)
        def send(self,status,value,mime='application/json'):
            body=json.dumps(value,allow_nan=False).encode() if mime=='application/json' else value
            self.send_response(status)
            for k,v in {'Content-Type':mime,'Content-Length':str(len(body)),'Cache-Control':'no-store',
                        'X-Content-Type-Options':'nosniff',
                        'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'"}.items():self.send_header(k,v)
            self.end_headers()
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
        def valid_host(self):
            return len(self.headers.get_all('Host',[]))==1 and self.headers.get('Host') in (f'localhost:{self.server.server_port}',f'127.0.0.1:{self.server.server_port}')
        def dispatch(self):
            if not self.valid_host():return self.send(403,envelope(error=Fault('ACCESS_DENIED','Use loopback host')))
            origin=self.headers.get('Origin')
            if origin and origin!='http://'+self.headers.get('Host'):return self.send(403,envelope(error=Fault('ACCESS_DENIED','Origin not permitted')))
            path=urlsplit(self.path).path
            if path.startswith('/v1/') and urlsplit(self.path).query:
                return self.send(400,envelope(error=Fault('SCHEMA_INVALID','Query parameters are not supported on this route')))
            if self.command=='GET':
                if path=='/':return self.send(200,(Path(__file__).parents[1]/'ui/platform.html').read_bytes(),'text/html; charset=utf-8')
                if path in ('/platform.mjs','/adapter-form.mjs','/asset-import.mjs','/run-details.mjs','/result-view.mjs','/result-summary.mjs','/response-json.mjs','/app.css','/platform.css'):
                    return self.send(200,(Path(__file__).parents[1]/'ui'/path[1:]).read_bytes(),'text/javascript' if path.endswith('mjs') else 'text/css')
                if path=='/bootstrap':
                    return self.send(403,envelope(error=Fault('ACCESS_DENIED','Organization login required'))) if gateway else self.send(200,{'token':token})
                if path=='/openapi.json':return self.send(200,openapi())
                if path == '/docs':
                    self.send_response(302); self.send_header('Location', '/docs/'); self.end_headers(); return
                name = 'docs/index.html' if path == '/docs/' else path.lstrip('/')
                if name in self.server.documentation:
                    raw, mime = self.server.documentation[name]
                    return self.send(200, json.loads(raw) if mime == 'application/json' else raw, mime)
                if path.startswith('/docs/'):
                    return self.send(404, envelope(error=Fault('NOT_FOUND', 'Documentation page not found')))
            provided=self.headers.get('Authorization','')
            active=service
            if gateway and path=='/v1/organization/login' and self.command=='POST':
                active=gateway
            elif gateway:
                try:
                    if not provided.startswith('Bearer '):raise Fault('ACCESS_DENIED','Bearer token required')
                    active=gateway.authenticate(provided[7:])
                except Fault as exc:return self.send(401,envelope(error=exc))
            elif not hmac.compare_digest(provided,'Bearer '+token):return self.send(401,envelope(error=Fault('ACCESS_DENIED','Valid bearer token required')))
            try:
                if self.command=='GET':
                    match=re.fullmatch(r'/v1/results/([a-f0-9]{32})/artifacts/([a-z0-9.-]+)',path)
                    if match:
                        if active.identity:active.identity.check('get_result')
                        return self.send(200,active.artifact(*match.groups()),'application/octet-stream')
                for name,spec in OPERATIONS.items():
                    if spec['method']!=self.command:continue
                    fields=re.findall(r'\{(\w+)\}',spec['route'])
                    pattern=re.escape(spec['route'])
                    for field in fields:pattern=pattern.replace(re.escape('{'+field+'}'),r'([^/]+)')
                    match=re.fullmatch(pattern,path)
                    if not match:continue
                    args={}
                    if self.command=='POST':
                        if self.headers.get('Content-Type')!='application/json' or self.headers.get('Transfer-Encoding'):
                            raise Fault('SCHEMA_INVALID','Send a JSON body with Content-Length')
                        if len(self.headers.get_all('Content-Length',[]))!=1:raise Fault('SCHEMA_INVALID','Missing body length')
                        length=int(self.headers['Content-Length'])
                        if not 0<length<=2*1024*1024:raise Fault('SCHEMA_INVALID','Body exceeds 2 MiB')
                        args=json.loads(self.rfile.read(length),object_pairs_hook=strict_object,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
                        if not isinstance(args,dict):raise Fault('SCHEMA_INVALID','Expected an object')
                    if set(args)&set(fields):raise Fault('SCHEMA_INVALID','Path fields must not be repeated in body')
                    args.update(zip(fields,map(unquote,match.groups())))
                    result=active.call(name,args)
                    status=200
                    if result['status']=='error':status=403 if result['error']['code']=='ACCESS_DENIED' else 400
                    return self.send(status,result)
                self.send(404,envelope(error=Fault('NOT_FOUND','Route not found')))
            except (ValueError,KeyError,OSError) as exc:
                self.send(400,envelope(error=exc if isinstance(exc,Fault) else Fault('SCHEMA_INVALID','Invalid request or unavailable artifact')))
        do_GET=dispatch
        do_POST=dispatch
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    from .documentation import documents
    try:
        server.documentation=documents(docs_base_url or f'http://127.0.0.1:{server.server_port}')
    except BaseException:
        server.server_close()
        raise
    server.daemon_threads=True
    server.token=token
    return server
