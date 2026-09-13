"""CI result export over the ordinary authenticated SDK; no implicit execution."""
import argparse
import json
from pathlib import Path

from .placement import connect
from .contracts import Fault


def export_report(client, result_id, destination):
    destination = Path(destination)
    # A denied/new failed fetch must never leave an old passing CI report.
    for name in ('worldfuzz.json','worldfuzz.junit.xml'):
        (destination/name).unlink(missing_ok=True)
    response = client.call('get_result_report', {'result_id': result_id})
    if response['status'] != 'ok':
        raise Fault(response['error']['code'], response['error']['message'])
    report = response['data']
    destination.mkdir(parents=True, exist_ok=True)
    (destination/'worldfuzz.json').write_text(json.dumps(report['summary'], indent=2)+'\n', encoding='utf-8')
    (destination/'worldfuzz.junit.xml').write_text(report['junit_xml']+'\n', encoding='utf-8')
    return report['summary']['exit_code']


def main(argv=None):
    parser = argparse.ArgumentParser(description='Export one authorized WorldFuzz result to CI JSON/JUnit')
    parser.add_argument('--placement', type=Path, required=True)
    parser.add_argument('--result-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return export_report(connect(args.placement), args.result_id, args.output)
    except (Fault, OSError, ValueError) as exc:
        print(json.dumps({'status':'error', 'code':getattr(exc,'code','CI_EXPORT_FAILED')}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
