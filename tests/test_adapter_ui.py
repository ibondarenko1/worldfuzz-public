"""UI logic on a DOM stand-in with actual HTTP and installed adapter worker, not visual QA."""
import os
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from worldfuzz.platform.service import Service
from worldfuzz.platform.http import make_server

class AdapterUITests(unittest.TestCase):
    def test_registered_adapter_form_to_real_worker(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'WORLDFUZZ_ADAPTERS':'trajectory-extent==0.1.0'}):
            service=Service(Path(tmp)/'results',Path(tmp)/'catalog.sqlite3')
            asset=service.register_asset(name='authored UI acceptance fixture',source_url='urn:test',data_license='fixture only',code_license='MIT',frame='shared',units='m',prediction=[[1,2,-3,4],[2,5,6,1]],reference=[[1,2,-3,4],[2,5,6,1]])
            extra={}
            if os.environ.get('WORLDFUZZ_UI_PUBLISHED_INPUTS'):
                inputs=Path(os.environ['WORLDFUZZ_UI_PUBLISHED_INPUTS'])
                item=json.loads((inputs/'episode.json').read_text())
                service.corpus.ingest(item['source'],[item['episode']])
                saved=service.corpus.episode(item['id'],'manipulation')
                self.assertEqual(saved['payload_sha256'],item['payload_sha256'])
                self.assertEqual(saved['split'],item['split'])
                sensor=service.register_sensor_recording(**json.loads((inputs/'sensor.json').read_text()),mcap_base64=base64.b64encode((inputs/'sample.mcap').read_bytes()).decode())
                extra={'WORLDFUZZ_UI_EPISODE':item['id'],'WORLDFUZZ_UI_SENSOR':sensor['asset_id']}
            server=make_server(service,0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                result=subprocess.run(['node','tests/test_adapter_ui.mjs'],cwd=Path(__file__).resolve().parents[1],env=os.environ|extra|{'WORLDFUZZ_UI_TEST_URL':f'http://127.0.0.1:{server.server_port}','WORLDFUZZ_UI_TEST_ASSET':asset['asset_id']},capture_output=True,text=True,timeout=75)
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                if extra:print(result.stdout)
            finally:
                server.shutdown();server.server_close();thread.join()
                for child in service.worker_processes:
                    if child.poll() is None:child.terminate()
                    child.wait(timeout=5)
