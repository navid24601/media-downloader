r"""Developer-only UI fixture. No Instagram access and no imported credentials.

Run: .venv\Scripts\python.exe tests\ui_fixture.py
Then open http://127.0.0.1:8795/#ui-fixture-capability
"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from instagram_local.store import Store
from instagram_local.worker import Worker
from instagram_local.server import Application, make_server
from test_app import PlainTestVault

root=Path(__file__).resolve().parents[1]/'test-output'
root.mkdir(exist_ok=True)
store=Store(root/'ui.sqlite3')
store.set_setting('folder',str(root/'downloads'))
worker=Worker(store,PlainTestVault()) # Deliberately not started: fixture cannot download.
server=make_server(Application(store,PlainTestVault(),worker,'ui-fixture-capability'),8795)
print('UI fixture ready on 127.0.0.1:8795. No worker or Instagram connection.')
try:server.serve_forever()
except KeyboardInterrupt:pass
finally:server.server_close()
