"""Offline data ingestion and filtered, provenance-preserving export."""
import argparse
import json
from pathlib import Path
from .corpus import Corpus, DEFAULT_STORE, import_navigation, import_robomimic


def main(argv=None):
    parser = argparse.ArgumentParser(prog='worldfuzz data')
    parser.add_argument('--store', type=Path, default=DEFAULT_STORE)
    sub = parser.add_subparsers(dest='operation', required=True)
    nav = sub.add_parser('import-navigation')
    nav.add_argument('directory', type=Path)
    robot = sub.add_parser('import-robomimic')
    robot.add_argument('file', type=Path)
    robot.add_argument('--manifest', type=Path, required=True)
    sub.add_parser('list')
    export = sub.add_parser('export')
    export.add_argument('--domain', choices=['navigation','manipulation'], required=True)
    export.add_argument('--status', choices=['OUTCOME_VERIFIED','STRUCTURE_VALIDATED'])
    export.add_argument('--label')
    export.add_argument('--split', choices=['train','validation','test'])
    export.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    store = Corpus(args.store)
    if args.operation == 'import-navigation':
        result = import_navigation(store, args.directory)
    elif args.operation == 'import-robomimic':
        result = import_robomimic(store, args.file, json.loads(args.manifest.read_text()))
    elif args.operation == 'export':
        payload = store.export(args.domain, args.status, args.label, args.split)
        with args.out.open('xb') as target:
            target.write(payload)
        result = {'episodes': len(payload.splitlines()), 'out': str(args.out), 'training_performed': False}
    else:
        result = store.listing()
    print(json.dumps(result, indent=2))
    return 0
