"""Single-instance and occupied-port test, with a separate temporary vault."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import requests
from instagram_local.security import Vault
from app import verified_instance


@unittest.skipUnless(os.name=='nt','Windows single-instance launcher')
class LauncherTests(unittest.TestCase):
    def test_occupied_port_duplicate_launch_and_clean_shutdown(self):
        project=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            state=Path(temporary)/'state'
            occupied=socket.socket()
            try:
                occupied.bind(('127.0.0.1',8765));occupied.listen()
            except OSError:
                occupied.close();occupied=None
            proc=subprocess.Popen([sys.executable,str(project/'app.py'),'--no-browser','--state-dir',str(state)],cwd=project,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                vault=Vault(state/'runtime.bin')
                info=None
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    if proc.poll() is not None:
                        out,err=proc.communicate()
                        self.fail('Fixture launcher exited before readiness: '+out)
                    if vault.path.exists():
                        info=vault.load()
                        if verified_instance(info):break
                    time.sleep(.1)
                self.assertIsNotNone(info)
                self.assertNotEqual(info['port'],8765)
                duplicate=subprocess.run([sys.executable,str(project/'app.py'),'--no-browser','--state-dir',str(state)],cwd=project,capture_output=True,text=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(duplicate.returncode,0)
                self.assertIn('already running',duplicate.stdout)
                self.assertNotIn(info['token'],duplicate.stdout)
                session=requests.Session();session.trust_env=False
                url=f"http://127.0.0.1:{info['port']}"
                headers={'Origin':url,'X-App-Token':info['token']}
                data=session.post(url+'/api/state',json={},headers=headers,timeout=5).json()
                self.assertFalse(data['worker']['active'])
                self.assertEqual(session.post(url+'/api/shutdown',json={},headers=headers,timeout=5).status_code,200)
                proc.communicate(timeout=10)
                self.assertEqual(proc.returncode,0)
                self.assertFalse(vault.path.exists())
                session.close()
            finally:
                if proc.poll() is None:proc.terminate();proc.communicate(timeout=10)
                if occupied:occupied.close()
