"""Read-only, content-addressed supplement for the accepted Can Paired source."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from ..contracts import Fault

ROOT = Path(__file__).resolve().parents[2] / 'benchmark_data' / 'can-replay-v1'
SOURCE_HASH = '5ad06c3591d929eadddc8a7511200a08a58686bfbb22691e966398f0fa005d66'
RUNTIME = {'robosuite': '1.5.1', 'mujoco': '3.2.7', 'numpy': '2.2.6',
           'numba': '0.61.2', 'mink': '0.0.13'}
ALLOCATION = {'source_njmax': '5000', 'source_nconmax': '5000', 'runtime_memory': '64M',
              'reason': 'Replace deprecated quadratic arena heuristic with explicit allocation; reject any engine warning'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def layout(xml):
    """Zero-based, half-open offsets; never treat a flattened state as qpos alone."""
    root = ET.fromstring(xml)
    if root.findall('.//include') or root.findall('.//default/joint'):
        raise ValueError('This supplement requires explicit, self-contained joint types')
    joints = []; nq = nv = 0
    for node in root.findall('./worldbody//joint'):
        kind = node.get('type', 'hinge')
        sizes = {'hinge': (1, 1), 'slide': (1, 1), 'ball': (4, 3), 'free': (7, 6)}
        a, b = sizes[kind]
        joints.append({'name': node.attrib['name'], 'type': kind, 'qpos_adr': nq,
                       'qvel_adr': nv, 'qpos_size': a, 'qvel_size': b})
        nq += a; nv += b
    for item in joints:
        item['state_qpos_slice'] = [1 + item['qpos_adr'], 1 + item['qpos_adr'] + item['qpos_size']]
        item['state_qvel_slice'] = [1 + nq + item['qvel_adr'], 1 + nq + item['qvel_adr'] + item['qvel_size']]
    return {'order': ['time', 'qpos', 'qvel'], 'time_slice': [0, 1], 'nq': nq, 'nv': nv,
            'state_size': 1 + nq + nv, 'joints': joints,
            'semantics': {'time': 'seconds', 'hinge': 'qpos rad; qvel rad/s',
                'slide': 'qpos m; qvel m/s',
                'free': 'qpos: world xyz m, quaternion wxyz; qvel: translation m/s, rotation rad/s',
                'ball': 'qpos quaternion wxyz; qvel rotation rad/s'},
            'original_mujoco_version': None, 'physical_calibration': None}


def extract(hdf5, target=ROOT):
    import h5py  # Optional: extraction does not import a simulator or read episode arrays.
    hdf5 = Path(hdf5); target = Path(target)
    with hdf5.open('rb') as stream:
        observed = hashlib.file_digest(stream, 'sha256').hexdigest()
    if observed != SOURCE_HASH:
        raise ValueError('HDF5 is not the accepted source')
    files = {}; demos = {}
    with h5py.File(hdf5, 'r') as source:
        data = source['data']
        env_raw = data.attrs['env_args'].encode('utf-8')
        files['env_args.json'] = env_raw
        for key in sorted(data, key=lambda k: int(k.removeprefix('demo_'))):
            demo = data[key]; raw = demo.attrs['model_file'].encode('utf-8')
            name = sha(raw) + '.xml'; files[name] = raw
            ep_meta = demo.attrs.get('ep_meta')
            if isinstance(ep_meta, bytes): ep_meta = ep_meta.decode('utf-8')
            demos[key] = {'model_file': name, 'model_sha256': sha(raw),
                          'ep_meta_present': 'ep_meta' in demo.attrs, 'ep_meta': ep_meta,
                          'states_shape': list(demo['states'].shape),
                          'actions_shape': list(demo['actions'].shape)}
    manifest = {'schema_version': '1.0.0', 'source_sha256': observed,
                'source_bytes': hdf5.stat().st_size, 'env_args_sha256': sha(env_raw),
                'demos': demos, 'models': {name: layout(raw) for name, raw in files.items() if name.endswith('.xml')},
                'unknowns': ['ep_meta absent where marked', 'original MuJoCo version', 'physical calibration'],
                'corpus_mutated': False}
    files['manifest.json'] = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    target.mkdir(parents=True, exist_ok=True)
    for name, raw in files.items():
        path = target / name
        if path.exists() and path.read_bytes() != raw:
            raise ValueError('Supplement version already exists with different bytes')
        path.write_bytes(raw)
    return {'supplement_sha256': sha(files['manifest.json']), 'demos': len(demos),
            'models': len(manifest['models']), 'source_sha256': observed}


def load():
    raw = (ROOT / 'manifest.json').read_bytes(); manifest = json.loads(raw)
    env_raw = (ROOT / 'env_args.json').read_bytes()
    if manifest['source_sha256'] != SOURCE_HASH or sha(env_raw) != manifest['env_args_sha256']:
        raise Fault('INTEGRITY_FAILED', 'Can supplement source or environment hash differs')
    for name, expected in manifest['models'].items():
        if Path(name).name != name or sha((ROOT / name).read_bytes()) + '.xml' != name:
            raise Fault('INTEGRITY_FAILED', 'Can XML hash differs')
        if layout((ROOT / name).read_bytes()) != expected:
            raise Fault('PROFILE_INCOMPATIBLE', 'Can state layout differs')
    env = json.loads(env_raw); kwargs = env['env_kwargs']
    controller = kwargs['controller_configs']['body_parts']['right']
    if (env['env_name'] != 'PickPlaceCan' or env['env_version'] != '1.5.1'
            or kwargs['control_freq'] != 20 or controller['type'] != 'OSC_POSE'
            or controller.get('input_ref_frame') != 'world' or controller.get('control_delta') is not True
            or any(kwargs[k] for k in ('has_renderer', 'has_offscreen_renderer', 'use_camera_obs'))):
        raise Fault('PROFILE_INCOMPATIBLE', 'Required Can controller, frame or headless settings are unsupported')
    return manifest, env, sha(raw)


def runtime_pin():
    """Check installed capability without importing MuJoCo/robosuite in the service."""
    from importlib import metadata
    import sys
    for name, version in RUNTIME.items():
        try: actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            raise Fault('RUNNER_UNSUPPORTED', 'Install the pinned optional Can replay runtime') from None
        if actual != version:
            raise Fault('RUNNER_UNSUPPORTED', name + ' must be ' + version)
    if sys.platform == 'win32':
        native = Path(metadata.distribution('mujoco').locate_file('mujoco/mujoco.dll'))
        placed = Path(metadata.distribution('robosuite').locate_file('robosuite/utils/mujoco.dll'))
        if not placed.is_file() or not native.is_file() or sha(placed.read_bytes()) != sha(native.read_bytes()):
            raise Fault('RUNNER_UNSUPPORTED', 'Complete isolated Windows robosuite DLL placement before preparing replay')
    return dict(RUNTIME)


def relocated_xml(name):
    from importlib import metadata
    root = ET.fromstring((ROOT / name).read_bytes())
    package = Path(metadata.distribution('robosuite').locate_file('robosuite')).resolve()
    assets = (package / 'models' / 'assets').resolve(); mapping = []
    size = root.find('size')
    if size is None or size.attrib != {'njmax': '5000', 'nconmax': '5000'}:
        raise Fault('PROFILE_INCOMPATIBLE', 'Unsupported source allocation declaration')
    # MuJoCo 3.2.7 treats njmax as a deprecated memory-sizing heuristic, not
    # a dynamics parameter. Only allocator configuration and asset paths change.
    del size.attrib['njmax']
    size.set('memory', ALLOCATION['runtime_memory'])
    for node in root.findall('.//*[@file]'):
        original = node.attrib['file']
        marker = '/robosuite/models/assets/'
        if marker not in original:
            raise Fault('PROFILE_INCOMPATIBLE', 'Unsupported XML asset locator')
        path = (assets / original.split(marker, 1)[1]).resolve()
        if not path.is_relative_to(assets) or not path.is_file():
            raise Fault('SOURCE_UNAVAILABLE', 'Pinned XML asset unavailable in robosuite 1.5.1')
        mapping.append({'original': original, 'package_path': path.relative_to(package).as_posix(),
                        'sha256': sha(path.read_bytes())})
        node.set('file', str(path))
    return ET.tostring(root, encoding='unicode'), mapping


def confirm_layout(model, expected):
    import mujoco
    if (model.nq, model.nv, model.na, model.njnt) != (37, 33, 0, len(expected['joints'])):
        raise Fault('PROFILE_INCOMPATIBLE', 'Compiled model dimensions differ from the 71-state contract')
    kinds = {0: 'free', 1: 'ball', 2: 'slide', 3: 'hinge'}
    actual = [{'name': mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, i),
               'qpos_adr': int(model.jnt_qposadr[i]), 'qvel_adr': int(model.jnt_dofadr[i]),
               'type': kinds[int(model.jnt_type[i])]} for i in range(model.njnt)]
    if actual != [{k: item[k] for k in actual[0]} for item in expected['joints']]:
        raise Fault('PROFILE_INCOMPATIBLE', 'Compiled joint addresses differ from the supplement')
    return {'nq': model.nq, 'nv': model.nv, 'na': model.na, 'joints': actual, 'state_size': 71,
            'arena_bytes': model.narena}
