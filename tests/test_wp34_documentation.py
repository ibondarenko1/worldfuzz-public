from contextlib import redirect_stdout
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from worldfuzz.platform import documentation as docs
from worldfuzz.platform.adapters import load_registry
from worldfuzz.platform.cli import main
from worldfuzz.platform.contracts import OPERATIONS, openapi, validate
from worldfuzz.platform.http import make_server
from worldfuzz.platform.service import Service


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__(); self.links = []; self.ids = set(); self.scripts = 0
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs: self.ids.add(attrs['id'])
        if tag == 'script': self.scripts += 1
        for key in ('href', 'src'):
            if key in attrs: self.links.append(attrs[key])


class DocumentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.origin = 'https://docs.example.invalid/worldfuzz'
        cls.files = docs.documents(cls.origin)

    def test_static_pages_links_and_sitemap(self):
        pages = {name: Page(raw.decode()) for name, (raw, mime) in self.files.items() if mime.startswith('text/html')}
        for name, page in pages.items():
            self.assertEqual(page.scripts, 0)
            for href in page.links:
                target = urlsplit(urljoin(self.origin + '/' + name, href))
                if target.netloc != 'docs.example.invalid': continue
                relative = target.path.removeprefix('/worldfuzz/')
                self.assertIn(relative, self.files, (name, href))
                if target.fragment: self.assertIn(target.fragment, pages[relative].ids, (name, href))
        xml = ET.fromstring(self.files['sitemap.xml'][0])
        locations = {node.text for node in xml.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')}
        self.assertEqual(locations, {self.origin + '/' + name for name in pages})
        self.assertIn(self.origin + '/docs/quickstart.html', self.files['llms.txt'][0].decode())

    def test_examples_cover_actual_bundled_operations_and_validate(self):
        with patch.dict(os.environ, {'WORLDFUZZ_ADAPTERS': 'not-installed==0'}):
            registry = load_registry(include_plugins=False)
        expected = {(d['profile']['id'], name) for d in registry.descriptors() for name in d['request_schemas']}
        examples = docs.workflows()
        self.assertEqual({(x['profile'], x['operation']) for x in examples}, expected)
        for item in examples:
            validate(docs.workflow_request(item), OPERATIONS['run_goal']['inputSchema'])
            registry.validate_request({'profile': item['profile'], 'operation': item['operation'], 'parameters': item['parameters']})
        self.assertEqual(json.loads(self.files['docs/openapi.json'][0]), openapi())
        api = Page(self.files['docs/api.html'][0].decode())
        self.assertTrue(set(OPERATIONS) <= api.ids)

    def test_configuration_and_export_do_not_create_a_workspace(self):
        for bad in ('not-a-url', 'http://public.example', 'https://user:secret@example.org', 'https://example.org/?token=secret'):
            with self.assertRaises(ValueError): docs.base_url(bad)
        with tempfile.TemporaryDirectory() as temp, patch('worldfuzz.platform.cli.Service') as service, redirect_stdout(io.StringIO()):
            output = Path(temp) / 'site'
            self.assertEqual(main(['docs', '--out', str(output), '--base-url', self.origin]), 0)
            service.assert_not_called()
            self.assertEqual({p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()}, set(self.files))
            with self.assertRaises(FileExistsError): docs.export(output, self.origin)

    def test_public_docs_preserve_task_authentication(self):
        with tempfile.TemporaryDirectory() as temp:
            service = Service(Path(temp) / 'results', Path(temp) / 'catalog.sqlite3', launch=False)
            server = make_server(service)
            thread = threading.Thread(target=server.serve_forever); thread.start()
            try:
                url = f'http://127.0.0.1:{server.server_port}'
                for route in ('/docs', '/docs/quickstart.html', '/docs/openapi.json', '/llms.txt', '/sitemap.xml'):
                    with urlopen(url + route, timeout=5) as response: self.assertEqual(response.status, 200)
                with self.assertRaises(HTTPError) as denied: urlopen(url + '/v1/capabilities', timeout=5)
                self.assertEqual(denied.exception.code, 401)
                denied.exception.close()
                with self.assertRaises(HTTPError) as missing: urlopen(url + '/docs/no-such-page.html', timeout=5)
                self.assertEqual(missing.exception.code, 404)
                missing.exception.close()
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=5)
            self.assertFalse(thread.is_alive())


class BuiltDocumentationTests(unittest.TestCase):
    def test_new_wheel_contains_every_documentation_dependency(self):
        wheel = os.environ.get('WORLDFUZZ_DOCS_WHEEL')
        if not wheel: self.skipTest('Run separately with the new WP34 wheel; not an installation matrix')
        with ZipFile(wheel) as archive:
            for path in docs.ROOT.iterdir():
                if path.is_file():
                    self.assertEqual(archive.read('worldfuzz/documentation/' + path.name), path.read_bytes())
            self.assertIn('worldfuzz/platform/documentation.py', archive.namelist())


if __name__ == '__main__':
    unittest.main()
