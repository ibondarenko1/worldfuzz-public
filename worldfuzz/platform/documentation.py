"""Plain HTML documentation from packaged prose and the live public contracts.

No workspace, token, model, external fetch or second schema source is involved.
The same renderer serves the existing HTTP docs and exports a static directory.
"""
from html import escape
import json
from pathlib import Path
from pprint import pformat
from urllib.parse import urlsplit
from xml.sax.saxutils import escape as xml_escape

from .. import __version__
from .contracts import OPERATIONS, REQUEST_EXAMPLES, VERSION, openapi, output_schema

ROOT = Path(__file__).parents[1] / 'documentation'
PAGES = {
    'index': ('WorldFuzz documentation', 'Run bounded robotics tasks, keep evidence and reuse checked data.'),
    'quickstart': ('Run your first published task', 'One local Moving AI task, a checked result and an export.'),
    'connect': ('Connect HTTP, SDK, CLI and MCP', 'Choose a client for the same authorized platform service.'),
    'tasks': ('Supported tasks and inputs', 'Exact operations, requirements and limits of evidence.'),
    'compare': ('Compare completed results', 'Read compatible saved results without inventing a new run.'),
    'export': ('Export results and datasets', 'Keep artifacts, groups, splits, rights and provenance.'),
    'errors': ('Errors and evidence limits', 'Resolve missing inputs and distinguish execution from task outcomes.'),
    'api': ('API and operation reference', 'Generated from the current shared service contracts.'),
    'profiles': ('Profile reference', 'Generated from the bundled adapter descriptors and parameter schemas.'),
}


def base_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment or '\\' in value or
            any(ord(c) <= 32 for c in value) or any(p in ('.', '..') for p in parsed.path.split('/'))):
        raise ValueError('Documentation base URL must be an absolute HTTP(S) URL without credentials, query or fragment')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '[::1]', '::1'):
        raise ValueError('Use HTTPS for a publication domain, or loopback HTTP for local preview')
    return value.rstrip('/')


def code(value):
    return '<pre><code>' + escape(value) + '</code></pre>'


def workflows():
    return json.loads((ROOT / 'workflows.json').read_text(encoding='utf-8'))


def workflow_request(item):
    return {'goal': {'operation': item['operation'], 'profile': item['profile'],
                    'parameters': item['parameters'], 'max_seconds': item.get('seconds', 30),
                    'resource_limits': {'cpu_seconds': item.get('cpu', 20),
                        'memory_bytes': item.get('memory_mib', 256) * 1024**2,
                        'artifact_bytes': item.get('artifact_mib', 8) * 1024**2},
                    'verification': {'mode': item['mode']}},
            'idempotency_key': 'docs-' + item['id'] + '-v1', 'wait_seconds': 20}


def tasks_html():
    sections = []
    for item in workflows():
        sections.append('<section id="' + escape(item['id']) + '"><h2>' + escape(item['title']) + '</h2>')
        sections.append('<p><strong>Adapter operation:</strong> <code>' + escape(item['operation']) +
            '</code> · <strong>Profile:</strong> <a href="profiles.html#' + escape(item['profile']) + '">' + escape(item['profile']) + '</a></p>')
        for label, field in (('Input', 'inputs'), ('Requirements', 'requirements'), ('Result and verification', 'evidence')):
            sections.append('<p><strong>' + label + ':</strong> ' + escape(item[field]) + '</p>')
        sections.append('<p>Example using the authenticated <code>client</code> from <a href="connect.html">Connect</a>. Replace marked IDs with accessible resources; this code starts a new bounded operation.</p>')
        sections.append(code('reply = client.call("run_goal", ' + pformat(workflow_request(item), sort_dicts=False) + ')'))
        sections.append('</section>')
    return ''.join(sections)


def api_html():
    parts = ['<p><a href="openapi.json">Download current OpenAPI JSON</a>. These ' + str(len(OPERATIONS)) +
             ' service operation names are also MCP tool names. Adapter operations are parameters to these tools, not additional MCP tools.</p>',
             '<p>Examples below come from the contract examples. Marked IDs, passwords and source metadata are placeholders; authored fixtures are not published benchmark results.</p>']
    parts.append('<ul>' + ''.join('<li><a href="#' + escape(name) + '">' + escape(name) + '</a></li>' for name in OPERATIONS) + '</ul>')
    for name, spec in OPERATIONS.items():
        parts.extend(['<section id="' + escape(name) + '"><h2>' + escape(name) + '</h2><p>' + escape(spec['description']) + '</p>',
            code(spec['method'] + ' ' + spec['route']), '<p>Effect: <strong>' + escape(spec['effect']) + '</strong>.</p>',
            '<details><summary>Request schema</summary>' + code(json.dumps(spec['inputSchema'], indent=2)) + '</details>',
            '<details><summary>Response schema</summary>' + code(json.dumps(output_schema(name), indent=2)) + '</details>',
            '<details><summary>Contract example</summary>' + code(json.dumps(REQUEST_EXAMPLES[name], indent=2)) + '</details></section>'])
    return ''.join(parts)


def profiles_html():
    from .adapters import load_registry
    parts = ['<p>Bundled profile contracts. Listing a profile does not install its optional runtime, grant source rights or prove replay is available in this workspace. Use <code>describe_operation</code> and prepare a plan to check the current environment.</p>']
    for d in load_registry(include_plugins=False).descriptors():
        p = d['profile']
        parts.append('<section id="' + escape(p['id']) + '"><h2>' + escape(p['id']) + '</h2>')
        parts.append(code(json.dumps(p, indent=2)))
        parts.append('<h3>Runtime requirements</h3>' + code(json.dumps(d['environment_lock'], indent=2)))
        for operation, schema in d['request_schemas'].items():
            parts.append('<h3>' + escape(operation) + '</h3><details><summary>Adapter parameters</summary>' + code(json.dumps(schema, indent=2)) + '</details>')
        parts.append('<details><summary>Source rights and semantic requirements</summary>' + code(json.dumps(d['source_contract'], indent=2)) + '</details></section>')
    return ''.join(parts)


def documents(origin):
    origin = base_url(origin)
    navigation = '<nav aria-label="Documentation">' + ''.join(
        '<a href="' + name + '.html">' + escape(title) + '</a>' for name, (title, _) in PAGES.items()) + '</nav>'
    result = {}
    for name, (title, description) in PAGES.items():
        content = (ROOT / (name + '.html')).read_text(encoding='utf-8') if name not in ('api', 'profiles') else ''
        content = content.replace('{{version}}', __version__)
        if name == 'tasks': content += tasks_html()
        elif name == 'api': content = api_html()
        elif name == 'profiles': content = profiles_html()
        page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + escape(title) + ' | WorldFuzz</title><meta name="description" content="' + escape(description, quote=True) + '">'
            '<link rel="canonical" href="' + escape(origin + '/docs/' + name + '.html', quote=True) + '"><link rel="stylesheet" href="docs.css"></head>'
            '<body><a class="skip" href="#content">Skip to content</a><header><a class="brand" href="index.html">WorldFuzz</a>'
            '<span>Product ' + escape(__version__) + ' · API ' + VERSION + ' · Local documentation</span></header><div class="layout">' + navigation +
            '<main id="content"><h1>' + escape(title) + '</h1><p class="lead">' + escape(description) + '</p>' + content +
            '<footer>Prepared documentation; publication and indexing are not established. Offline software evidence is not physical-robot validation.</footer>'
            '</main></div></body></html>')
        result['docs/' + name + '.html'] = (page.encode(), 'text/html; charset=utf-8')
    result['docs/docs.css'] = ((ROOT / 'docs.css').read_bytes(), 'text/css; charset=utf-8')
    result['docs/openapi.json'] = ((json.dumps(openapi(), indent=2) + '\n').encode(), 'application/json')
    links = ['- [' + title + '](' + origin + '/docs/' + name + '.html): ' + description for name, (title, description) in PAGES.items()]
    llms = ('# WorldFuzz\n\n> Bounded local robotics tasks, independent checks, versioned data and a common HTTP/SDK/CLI/MCP service.\n\n'
        'Product ' + __version__ + '; API ' + VERSION + '. No published site, PyPI distribution or MCP registry listing is claimed.\n\n' + '\n'.join(links) +
        '\n- [OpenAPI](' + origin + '/docs/openapi.json): generated request/response contracts.\n\n'
        '## Agent execution rules\n\n'
        'Use find_resources for exact IDs. Moving AI indices are zero-based: movingai:8 is case 8.\n'
        'MCP server label worldfuzz is configuration, not part of a tool name. Call run_goal, not worldfuzz_worldfuzz_run_goal.\n'
        'run_goal can compile, prepare, execute, wait and report one typed operation. A waiting response retains its run_id; reuse its key and goal.\n'
        'prepare_plan, verify_result and export_result prepare only: no job or export file exists yet. Confirm exports using completed execution and checked artifacts.\n'
        'A campaign requires create_campaign, launch_campaign_case and finalize_campaign; separate jobs are not a completed campaign.\n'
        'Honor error.next_action, missing_parameters, source rights, workspace scope and budgets. Do not substitute unknown conversion, replay or held-out training with another task.\n'
        'Record run state, task outcome and proven_result separately. Integrity or recorded-state viewing is not action replay; software replay is not physical replay.\n'
        'Exact nanoseconds use decimal strings in the versioned transport representation. Stored hashes refer to original bytes.\n'
        'The 2x action-reduction goal and independent-agent certification are not established. No paid inference, training or device access is needed for the navigation quickstart.\n')
    result['llms.txt'] = (llms.encode(), 'text/plain; charset=utf-8')
    urls = [origin + '/docs/' + name + '.html' for name in PAGES]
    result['sitemap.xml'] = (('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' +
        ''.join('<url><loc>' + xml_escape(url) + '</loc></url>' for url in urls) + '</urlset>\n').encode(), 'application/xml')
    return result


def export(destination, origin):
    files = documents(origin)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    for name, (content, _) in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return {'directory': str(destination), 'files': list(files), 'version': __version__,
            'base_url': base_url(origin), 'published': False, 'indexed': False}
