"""Offline UI demo: real synthetic local media, no running download worker."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
import requests
from library_fixtures import generate
from test_app import PlainTestVault
from instagram_local.store import Store
from instagram_local.worker import Worker
from instagram_local.server import Application,make_server

root=Path(__file__).resolve().parents[1]/'test-output'/'library-v2'
files=generate(root/'media')
store=Store(root/'fixture.sqlite3')
for index,path in enumerate(files):
    code=['IMAGE01','VIDEO01','CAROUSEL1'][index]
    extras=[str(path.with_suffix('.txt'))] if index==0 else []
    with store.tx() as db:db.execute('INSERT OR REPLACE INTO downloads VALUES(?,?,?,?)',(code,str(path),json.dumps(extras),f'2026-10-{25+index}T10:00:00+00:00'))
store.set_setting('folder',str(root/'media'))
vault=PlainTestVault();worker=Worker(store,vault)
app=Application(store,vault,worker,'library-ui-fixture')
def blocked(*args,**kwargs):raise AssertionError('Outbound requests are disabled in this offline fixture')
requests.Session.request=blocked
app.library.start()
server=make_server(app,8794)
print('Offline Library fixture on 127.0.0.1:8794. Outbound Python requests disabled.')
try:server.serve_forever()
except KeyboardInterrupt:pass
finally:app.library.close();server.server_close();store.db.close()
