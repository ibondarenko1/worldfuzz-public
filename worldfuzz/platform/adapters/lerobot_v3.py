"""LeRobot v3.0 serialization with train-only normalization statistics."""
from copy import deepcopy
import json
import os

from ...corpus import digest, encoded
from ..contracts import Fault


OBSERVATION_STATE = 'robot0_joint_pos'
ARRAY_KEYS = {'actions', 'dones', 'next_obs', 'obs', 'rewards', 'states'}


def _feature(dtype, size):
    return {'dtype': dtype, 'shape': [size], 'names': None}


def _stats(rows, names):
    """Welford over train rows only; callers never pass held-out rows."""
    import numpy as np
    result = {}
    for name in names:
        values = np.asarray([row[name] for row in rows], dtype=np.float64)
        if values.ndim == 1:
            values = values[:, None]
        count = values.shape[0]
        mean = values.mean(axis=0)
        delta = values - mean
        result[name] = {'min': values.min(axis=0).tolist(), 'max': values.max(axis=0).tolist(),
            'mean': mean.tolist(), 'std': np.sqrt((delta * delta).sum(axis=0) / count).tolist(),
            'count': [count]}
    return result


def write_can(service, snapshot, root, checkpoint):
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    episodes = []
    rows = []
    features = {
        'timestamp': _feature('float64', 1), 'frame_index': _feature('int64', 1),
        'episode_index': _feature('int64', 1), 'index': _feature('int64', 1),
        'task_index': _feature('int64', 1), 'action': _feature('float64', 7),
        'worldfuzz.source_time': _feature('float64', 1),
        'worldfuzz.source_reward': _feature('float64', 1),
        'worldfuzz.source_done': _feature('int64', 1),
        'worldfuzz.simulator_state': _feature('float64', 71),
    }
    first = service._episode(snapshot['episodes'][0]['id'], 'manipulation')['episode']
    if OBSERVATION_STATE not in first['obs']:
        raise Fault('PROFILE_INCOMPATIBLE', 'Source has no robot0_joint_pos observation')
    for prefix, mapping in (('observation.', first['obs']), ('worldfuzz.next_observation.', first['next_obs'])):
        for name, values in sorted(mapping.items()):
            if not values or not isinstance(values[0], list):
                raise Fault('PROFILE_INCOMPATIBLE', 'Observation channels must be non-empty vectors')
            features[prefix + name] = _feature('float64', len(values[0]))
    # The canonical policy observation is a real source channel, not the 71-value simulator state.
    features['observation.state'] = _feature('float64', len(first['obs'][OBSERVATION_STATE][0]))

    absolute = 0
    for episode_index, pinned in enumerate(snapshot['episodes']):
        checkpoint()
        envelope = service._episode(pinned['id'], 'manipulation')
        episode = envelope['episode']
        length = len(episode['actions'])
        if any(len(episode[key]) != length for key in ('states', 'rewards', 'dones')):
            raise Fault('PROFILE_INCOMPATIBLE', 'Source arrays have different frame counts')
        if set(episode['obs']) != set(first['obs']) or set(episode['next_obs']) != set(first['next_obs']):
            raise Fault('PROFILE_INCOMPATIBLE', 'Observation channel set changed inside generation')
        if any(len(values) != length for values in list(episode['obs'].values()) + list(episode['next_obs'].values())):
            raise Fault('PROFILE_INCOMPATIBLE', 'Observation arrays have different frame counts')
        start = absolute
        for frame in range(length):
            row = {'timestamp': frame / 20.0, 'frame_index': frame, 'episode_index': episode_index,
                'index': absolute, 'task_index': 0, 'action': episode['actions'][frame],
                'observation.state': episode['obs'][OBSERVATION_STATE][frame],
                'worldfuzz.source_time': episode['states'][frame][0],
                'worldfuzz.source_reward': episode['rewards'][frame],
                'worldfuzz.source_done': episode['dones'][frame],
                'worldfuzz.simulator_state': episode['states'][frame]}
            row.update({'observation.' + key: values[frame] for key, values in episode['obs'].items()})
            row.update({'worldfuzz.next_observation.' + key: values[frame] for key, values in episode['next_obs'].items()})
            rows.append(row); absolute += 1
        nonarrays = {key: deepcopy(value) for key, value in episode.items() if key not in ARRAY_KEYS}
        episodes.append({'episode_index': episode_index, 'episode_id': pinned['id'],
            'payload_sha256': pinned['payload_sha256'], 'group_id': pinned['group_id'], 'split': pinned['split'],
            'source_sha256': pinned['source_sha256'], 'length': length, 'dataset_from_index': start,
            'dataset_to_index': absolute, 'non_array_payload': nonarrays,
            'observation_names': sorted(episode['obs']), 'next_observation_names': sorted(episode['next_obs'])})

    data_path = root / 'data/chunk-000/file-000.parquet'
    data_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), data_path, compression='snappy', use_dictionary=False,
                   row_group_size=max(1, len(rows)))
    tasks = pd.DataFrame([{'task': 'PickPlaceCan', 'task_index': 0}]).set_index('task')
    task_path = root / 'meta/tasks.parquet'; task_path.parent.mkdir(parents=True, exist_ok=True)
    tasks.to_parquet(task_path)
    episode_rows = [{'episode_index': item['episode_index'], 'length': item['length'],
        'tasks': ['PickPlaceCan'], 'dataset_from_index': item['dataset_from_index'],
        'dataset_to_index': item['dataset_to_index'], 'data/chunk_index': 0, 'data/file_index': 0,
        'meta/episodes/chunk_index': 0, 'meta/episodes/file_index': 0} for item in episodes]
    ep_path = root / 'meta/episodes/chunk-000/file-000.parquet'; ep_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(episode_rows).to_parquet(ep_path, index=False)
    split_ranges = {}
    for split in ('train', 'validation', 'test'):
        indices = [e['episode_index'] for e in episodes if e['split'] == split]
        if indices:
            # Selection is sorted by split, so native LeRobot ranges are exact and contiguous.
            split_ranges[split] = f'{min(indices)}:{max(indices)+1}'
    info = {'codebase_version': 'v3.0', 'fps': 20, 'features': features,
        'total_episodes': len(episodes), 'total_frames': len(rows), 'total_tasks': 1,
        'chunks_size': 1000, 'data_files_size_in_mb': 100, 'video_files_size_in_mb': 200,
        'data_path': 'data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet',
        'video_path': None, 'robot_type': 'Panda / PickPlaceCan', 'splits': split_ranges}
    (root / 'meta/info.json').write_text(encoded(info), encoding='utf-8')
    train_rows = [row for row in rows if episodes[row['episode_index']]['split'] == 'train']
    normalized = ['action'] + sorted(key for key in features if key.startswith('observation.'))
    (root / 'meta/stats.json').write_text(encoded(_stats(train_rows, normalized)), encoding='utf-8')
    provenance = {'schema_version': 1, 'episodes': episodes, 'normalization': {
        'source_split': 'train', 'episode_ids': [e['episode_id'] for e in episodes if e['split'] == 'train'],
        'excluded_splits': ['validation', 'test'], 'features': normalized},
        'observation_state_source': 'obs.robot0_joint_pos',
        'simulator_state_role': 'worldfuzz metadata feature; never observation.state',
        'controller': {'name': 'OSC_POSE', 'rate_hz': 20, 'input_frame': 'world'}}
    sidecar = root / 'worldfuzz/episodes.json'; sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(encoded(provenance), encoding='utf-8')


def _plain(value):
    if hasattr(value, 'tolist'):
        return value.tolist()
    return value


def verify_can(service, snapshot, root, checkpoint):
    # Force local-only behavior and preserve float64 values through the native torch conversion.
    cache = root / '.reader-cache'; cache.mkdir()
    os.environ['HF_HOME'] = str(cache)
    os.environ['HF_HUB_OFFLINE'] = '1'; os.environ['HF_DATASETS_OFFLINE'] = '1'
    import torch
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    previous = torch.get_default_dtype(); torch.set_default_dtype(torch.float64)
    try:
        reader = LeRobotDataset('worldfuzz/local-generation', root=root, revision='v3.0',
            download_videos=False, token=False)
        sidecar = json.loads((root / 'worldfuzz/episodes.json').read_text(encoding='utf-8'))
        if reader.num_episodes != len(snapshot['episodes']) or reader.num_frames != sum(e['length'] for e in sidecar['episodes']):
            raise Fault('VERIFICATION_FAILED', 'Native LeRobot reader returned different dimensions')
        if 'observation.state' not in reader.features or reader.features['observation.state']['shape'] != (7,):
            raise Fault('VERIFICATION_FAILED', 'LeRobot observation.state is not the source joint observation')
        if reader.features['worldfuzz.simulator_state']['shape'] != (71,):
            raise Fault('VERIFICATION_FAILED', 'Simulator state metadata dimension changed')
        for meta, pinned in zip(sidecar['episodes'], snapshot['episodes'], strict=True):
            checkpoint()
            source = service._episode(pinned['id'], 'manipulation')
            payload = deepcopy(meta['non_array_payload'])
            payload.update(actions=[], dones=[], next_obs={key: [] for key in meta['next_observation_names']},
                obs={key: [] for key in meta['observation_names']}, rewards=[], states=[])
            for index in range(meta['dataset_from_index'], meta['dataset_to_index']):
                item = reader.get_raw_item(index)
                payload['actions'].append(_plain(item['action']))
                payload['dones'].append(int(_plain(item['worldfuzz.source_done'])))
                payload['rewards'].append(float(_plain(item['worldfuzz.source_reward'])))
                payload['states'].append(_plain(item['worldfuzz.simulator_state']))
                for key in meta['observation_names']:
                    payload['obs'][key].append(_plain(item['observation.' + key]))
                for key in meta['next_observation_names']:
                    payload['next_obs'][key].append(_plain(item['worldfuzz.next_observation.' + key]))
                if _plain(item['observation.state']) != payload['obs'][OBSERVATION_STATE][-1]:
                    raise Fault('VERIFICATION_FAILED', 'Canonical observation does not match the source channel')
                if float(_plain(item['worldfuzz.source_time'])) != payload['states'][-1][0]:
                    raise Fault('VERIFICATION_FAILED', 'Source timestamp changed in LeRobot round trip')
            if digest(payload) != pinned['payload_sha256'] or payload != source['episode']:
                raise Fault('VERIFICATION_FAILED', 'LeRobot native-reader reconstruction changed source values')
            expected_meta = {**pinned, 'episode_id': pinned['id']}
            if any(meta[key] != expected_meta[key] for key in ('episode_id','payload_sha256','group_id','split','source_sha256')):
                raise Fault('VERIFICATION_FAILED', 'Group, split or provenance metadata changed')
        stats = json.loads((root / 'meta/stats.json').read_text(encoding='utf-8'))
        if sidecar['normalization']['source_split'] != 'train' or set(sidecar['normalization']['episode_ids']) != {
                p['id'] for p in snapshot['episodes'] if p['split'] == 'train'}:
            raise Fault('VERIFICATION_FAILED', 'Normalization source includes a held-out episode')
        return {'reader': 'lerobot.datasets.LeRobotDataset', 'reader_version': '0.6.1',
            'format': 'v3.0', 'episodes': reader.num_episodes, 'frames': reader.num_frames,
            'exact_roundtrip': True, 'normalization_split': 'train',
            'normalization_features': sorted(stats), 'simulator_state_used_as_observation': False}
    finally:
        torch.set_default_dtype(previous)
