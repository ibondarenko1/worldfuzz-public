import hashlib
import json
from pathlib import Path
import re
import tomllib
import unittest

try:
    import jsonschema
    import yaml
except ImportError:
    jsonschema = yaml = None

from worldfuzz.platform.contracts import OPERATIONS, validate
from worldfuzz.platform.adapters import load_registry

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(jsonschema is not None and yaml is not None, 'WP35 schema checks require jsonschema and PyYAML in the validation runtime')
class IntegrationSourceTests(unittest.TestCase):
    def test_official_registry_schema_and_package_identity(self):
        raw = (ROOT / 'integrations/mcp/schema/server-2025-12-11.schema.json').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), '3fba09590c99f61735d234822279f4223fab9e300c0a81e81c91ab62a4114de0')
        schema = json.loads(raw); jsonschema.Draft7Validator.check_schema(schema)
        server = json.loads((ROOT / 'server.json').read_text())
        jsonschema.Draft7Validator(schema, format_checker=jsonschema.FormatChecker()).validate(server)
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
        self.assertEqual(server['version'], project['version'])
        package, = server['packages']
        self.assertEqual((package['identifier'], package['version'], package['transport']),
                         (project['name'], project['version'], {'type': 'stdio'}))
        self.assertEqual(project['scripts']['worldfuzz'], 'worldfuzz.cli:main')
        self.assertIn('mcp-name: ' + server['name'], (ROOT / project['readme']).read_text())
        self.assertNotIn('remotes', server)
        self.assertEqual(package['packageArguments'][1]['name'], '--placement')
        self.assertTrue(package['packageArguments'][1]['isRequired'])

    def test_skill_format_and_live_navigation_contract(self):
        folder = ROOT / 'integrations/skills/worldfuzz'
        raw = (folder / 'SKILL.md').read_text()
        front, body = raw.split('---', 2)[1:]
        meta = yaml.safe_load(front)
        self.assertEqual(meta['name'], folder.name)
        self.assertRegex(meta['name'], r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
        self.assertTrue(1 <= len(meta['description']) <= 1024)
        self.assertTrue(all(isinstance(v, str) for v in meta['metadata'].values()))
        self.assertIn('references/navigation.md', body)
        example = (folder / 'references/navigation.md').read_text()
        request = json.loads(re.search(r'```json\n(.*?)\n```', example, re.S)[1])
        validate(request, OPERATIONS['run_goal']['inputSchema'])
        goal = request['goal']
        load_registry(include_plugins=False).validate_request({k: goal[k] for k in ('profile', 'operation', 'parameters')})


if __name__ == '__main__': unittest.main()
