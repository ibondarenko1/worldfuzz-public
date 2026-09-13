"""Local provenance-aware episode store. No inference, robot control or training."""
import hashlib
import json
from pathlib import Path

DEFAULT_STORE = Path('artifacts/corpus.sqlite3')


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def split_for(group):
    value = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 10
    return 'test' if value == 0 else 'validation' if value == 1 else 'train'


class Corpus:
    def __init__(self, path=DEFAULT_STORE, repository=None):
        from .storage import SQLiteRepository
        self.repository = repository or SQLiteRepository(path)
        self.path = self.repository.path
        self.repository.initialize()

    def connect(self):
        return self.repository.connect()

    def ingest(self, metadata, episodes, *, transaction_guard=None):
        """All-or-nothing import. Conflicting source identities are never overwritten."""
        dataset = digest(metadata)
        def records():
            for episode in episodes:
                payload=encoded(episode)
                yield {'id':digest([dataset,episode['source_key']]),'dataset':dataset,
                       **{k:episode[k] for k in ('source_key','domain','task','status','label','group_id')},
                       'proposed_split':split_for(episode['group_id']), 'payload':payload,
                       'sha256':hashlib.sha256(payload.encode()).hexdigest()}
        options={} if transaction_guard is None else {'transaction_guard':transaction_guard}
        added=self.repository.ingest_records(dataset,encoded(metadata),records(),**options)
        return {'dataset':dataset,'added':added,'total':len(self.listing()['episodes'])}

    def listing(self):
        data=self.repository.catalog_listing()
        episodes=data['episodes']
        datasets=[dict(id=row['id'],**json.loads(row['metadata'])) for row in data['datasets']]
        return {'schema_version': 1, 'datasets': datasets, 'episodes': episodes,
                'training_performed': False, 'provider_calls': 0}

    def export(self, domain, status=None, label=None, split=None):
        if domain not in ('navigation', 'manipulation'):
            raise ValueError('Select one domain; incompatible action spaces cannot be mixed')
        data=self.repository.export_records(domain=domain,status=status,label=label,split=split)
        rows=data['episodes']
        sources={r['id']:json.loads(r['metadata']) for r in data['datasets']}
        groups={r['group_id']:r['split'] for r in data['groups']}
        result = []
        for row in rows:
            payload = row['payload']
            if hashlib.sha256(payload.encode()).hexdigest() != row['sha256']:
                raise ValueError('Stored episode integrity check failed')
            episode = json.loads(payload)
            source = sources[row['dataset']]
            if (digest(source) != row['dataset'] or digest([row['dataset'], row['source_key']]) != row['id']
                    or any(episode[key] != row[key] for key in ('source_key','domain','task','status','label','group_id'))
                    or groups.get(episode['group_id']) != row['split']):
                raise ValueError('Stored provenance or filter index integrity check failed')
            result.append(encoded({'schema_version': 1, 'id': row['id'], 'split': row['split'],
                                   'source': source, 'payload_sha256': row['sha256'], 'episode': episode}))
        if not result:
            raise ValueError('No episodes match these export filters')
        return ('\n'.join(result) + '\n').encode()

    def episode(self, episode_id, domain):
        """Read one exact episode without expanding the rest of a large corpus."""
        if domain not in ('navigation', 'manipulation'):
            raise ValueError('Unsupported episode domain')
        data=self.repository.export_records(domain=domain,episode_id=episode_id)
        if len(data['episodes'])!=1:
            raise KeyError(episode_id)
        row=data['episodes'][0]
        source_row=next((value for value in data['datasets'] if value['id']==row['dataset']),None)
        group_row=next((value for value in data['groups'] if value['group_id']==row['group_id']),None)
        if source_row is None or group_row is None:
            raise ValueError('Stored episode references are incomplete')
        payload=row['payload'];episode=json.loads(payload);source=json.loads(source_row['metadata'])
        if (hashlib.sha256(payload.encode()).hexdigest()!=row['sha256'] or digest(source)!=row['dataset']
                or digest([row['dataset'],row['source_key']])!=row['id']
                or group_row['split']!=row['split']
                or any(episode[key]!=row[key] for key in ('source_key','domain','task','status','label','group_id'))):
            raise ValueError('Stored episode integrity check failed')
        return {'schema_version':1,'id':row['id'],'split':row['split'],'source':source,
                'payload_sha256':row['sha256'],'episode':episode}


def import_navigation(store, directory):
    from .published_benchmarks import load_cases, check_route, sha
    directory = Path(directory)
    report = json.loads((directory / 'report.json').read_text())
    provenance, _, maps, cases = load_cases()
    if len(report['cases']) != len(cases):
        raise ValueError('Report must contain the complete published selection')
    for source in provenance['sources']:
        if sha(directory / 'inputs' / source['file']) != source['sha256']:
            raise ValueError('Recorded input differs from pinned published source')
    episodes = []
    for record, expected in zip(report['cases'], cases):
        if record['case'] != expected or record['status'] != 'COMPLETED':
            raise ValueError('Report case differs from published selection')
        route = record['route']
        if (not isinstance(route, list) or len(route) > expected['width'] * expected['height']
                or any(not isinstance(p, list) or len(p) != 2 or
                       any(type(v) is not int for v in p) for p in route)):
            raise ValueError('Invalid grid route')
        check = check_route(maps[expected['map']], expected, route)
        if not check['route_valid'] or not check['matches_published_optimum']:
            raise ValueError('Route does not independently match the published outcome')
        episodes.append({'source_key': f"{expected['scenario_file']}:{expected['data_row']}",
                         'domain': 'navigation', 'task': 'octile_shortest_path',
                         'status': 'OUTCOME_VERIFIED', 'label': 'optimal_route',
                         'label_origin': 'independent_grid_check_against_published_length',
                         'group_id': 'movingai:' + sha(directory / 'inputs' / expected['map']),
                         'case': expected, 'states': route,
                         'actions': [[b[0]-a[0], b[1]-a[1]] for a,b in zip(route,route[1:])],
                         'state_semantics': 'grid_x_y', 'action_semantics': 'grid_delta_x_y',
                         'check': check})
    return store.ingest({'name': 'Moving AI street cases', 'version': 'pinned-original-selection-v1',
                         'source_url': 'https://www.movingai.com/benchmarks/street/index.html',
                         'license': 'ODC-By-1.0', 'attribution': provenance,
                         'adapter': 'movingai-v1'}, episodes)


def import_robomimic(store, path, manifest):
    """Import numeric low-dimensional demonstrations, without executing env metadata."""
    import h5py
    import numpy as np
    path = Path(path)
    if path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError('Dataset exceeds the 256 MiB import cap')
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    if checksum != manifest['sha256']:
        raise ValueError('Dataset checksum differs from manifest')
    episodes = []
    with h5py.File(path, 'r') as source:
        def local(parent, key):
            if not isinstance(parent.get(key, getlink=True), h5py.HardLink):
                raise ValueError('External and symbolic HDF5 links are unsupported')
            return parent[key]
        data = local(source, 'data')
        if not 1 <= len(data) <= 10000:
            raise ValueError('Invalid episode count')
        environment = json.loads(data.attrs['env_args'])
        if environment.get('env_name') != 'PickPlaceCan' or environment.get('env_version') != '1.5.1':
            raise ValueError('This adapter currently supports Can Paired / robosuite 1.5.1 only')
        total_values = 0
        for key in sorted(data):
            demo = local(data, key)
            arrays = {}
            for field in ('states', 'actions', 'rewards', 'dones'):
                node = local(demo, field)
                if (not isinstance(node, h5py.Dataset) or node.is_virtual or node.external
                        or node.dtype.kind not in 'fiu' or node.size > 2000000):
                    raise ValueError('Unsupported numeric episode array')
                total_values += node.size
                if total_values > 20000000:
                    raise ValueError('Expanded dataset exceeds import budget')
                arrays[field] = node[()]
                if not np.isfinite(arrays[field]).all():
                    raise ValueError('Non-finite episode data')
            states, actions, rewards = (arrays[k] for k in ('states','actions','rewards'))
            if (states.ndim != 2 or actions.ndim != 2 or rewards.ndim != 1 or
                    not 1 <= len(actions) <= 10000 or len(states) != len(actions) or len(rewards) != len(actions)):
                raise ValueError('Inconsistent episode dimensions')
            observations = {}
            for field in ('obs', 'next_obs'):
                observations[field] = {}
                group_node = local(demo, field)
                if len(group_node) > 64:
                    raise ValueError('Too many observation channels')
                for channel in group_node:
                    node = local(group_node, channel)
                    if (not isinstance(node, h5py.Dataset) or node.is_virtual or node.external
                            or node.dtype.kind not in 'fiu' or node.ndim != 2
                            or node.shape[0] != len(actions) or node.size > 2000000):
                        raise ValueError('Invalid observation channel')
                    total_values += node.size
                    if total_values > 20000000:
                        raise ValueError('Expanded dataset exceeds import budget')
                    values = node[()]
                    if not np.isfinite(values).all():
                        raise ValueError('Non-finite observations')
                    observations[field][channel] = values.tolist()
            if arrays['dones'].shape != rewards.shape:
                raise ValueError('Inconsistent terminal flags')
            # Round only split keys to 12 decimals: source pairs differ by up to floating-point epsilon.
            # Preserve all original state values in the payload; never group by labels or numbering.
            group = digest({'environment': environment, 'initial_state': [float(v) if v else 0.0 for v in np.round(states[0], 12)]})
            episodes.append({'source_key': key, 'domain': 'manipulation',
                'task': environment.get('env_name', 'unknown'), 'status': 'STRUCTURE_VALIDATED',
                'label': 'source_reward_positive' if bool((rewards > 0).any()) else 'source_reward_zero',
                'label_origin': 'published_sparse_reward_not_independently_replayed',
                'group_id': 'robomimic:' + group, 'environment': environment,
                'state_semantics': 'robosuite_simulator_state_not_policy_observation',
                'action_semantics': 'source_environment_controller_actions',
                **observations, **{k: v.tolist() for k,v in arrays.items()}})
    return store.ingest({**manifest, 'adapter': 'robomimic-lowdim-v1', 'split_group_rule': 'environment + initial simulator state rounded to 12 decimals'}, episodes)
