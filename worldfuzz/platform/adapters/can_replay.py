"""Bounded headless action execution. Recorded states initialize the simulator once."""
from copy import deepcopy
import json
import os

from ...corpus import digest, encoded
from ..contracts import Fault, ID, obj
from ..source_contracts import m1_contract, requirements
from .can_metadata import ROOT, ALLOCATION, load, runtime_pin, relocated_xml, confirm_layout

PROFILE = 'robomimic.can-paired.action-replay.v1'
OPERATION = 'replay_can_actions'


def replay_contract():
    """Additional immutable source/profile versions; the accepted M1 entities stay intact."""
    manifest, env, version = load(); source = m1_contract('manipulation')
    supplement = deepcopy(source['asset'])
    supplement.update(asset_id=version, version='can-replay-metadata-v1',
        source_locator='worldfuzz/benchmark_data/can-replay-v1/manifest.json',
        observed_hash_or_null=version, size_or_null=(ROOT / 'manifest.json').stat().st_size,
        observed_at=0)
    profile = deepcopy(source['profile'])
    def field(name, size, unit, frame, semantic):
        return {'name': name, 'dtype': 'float64', 'shape': [size], 'unit': unit,
                'frame': frame, 'semantic': semantic, 'rate_hz': 20}
    profile.update(profile_id=PROFILE, version=version,
        components=[{'component_id': 'Panda-OSC_POSE-PickPlaceCan', 'kind': 'manipulation_simulator'}],
        state_schema=[field('time', 1, 's', None, 'flattened state index 0'),
            field('qpos', 37, None, None, 'indices 1:38; joint coordinates and free-body world poses; mixed units in XML supplement'),
            field('qvel', 33, None, None, 'indices 38:71; joint rates; free-body linear world and angular local velocities')],
        action_schema=[field('delta_position_input', 3, 'normalized', 'world', 'OSC_POSE input [-1,1], output +/-0.05 m'),
            field('delta_rotation_input', 3, 'normalized', 'world', 'OSC_POSE input [-1,1], output +/-0.5 rad axis angle'),
            field('gripper_input', 1, 'normalized', None, 'original GRIP scalar command; not a joint target')],
        observation_schema=[field('simulator_state', 71, None, 'world', 'time, qpos, qvel; not camera data')],
        frames=[{'frame_id': 'world', 'parent': None,
                 'convention': 'Exact XML simulation world; OSC_POSE input_ref_frame world'}],
        units=['s', 'm', 'rad', 'm/s', 'rad/s', 'normalized'],
        time_model={'unit': 's', 'clock': 'MuJoCo simulation time', 'synchronization': '20 Hz controller; original XML timestep'},
        unknown_fields=['original MuJoCo version', 'physical calibration', 'absent source ep_meta'])
    # Semantic corrections produce a new immutable profile version even when
    # the source supplement bytes remain identical.
    profile['version'] = digest({'supplement': version, 'profile': profile})
    source['profile'] = profile; source['supplements'] = [supplement]
    return source


def advance_actions(env, initial, actions, check, emit):
    """The only recorded-state write in execution. No later source states are accepted."""
    import numpy as np
    env.sim.set_state_from_flattened(np.asarray(initial, dtype=float))
    env.sim.forward()
    emit(0, env.sim.get_state().flatten().tolist())
    for index, action in enumerate(actions, 1):
        check()
        env.step(np.asarray(action, dtype=float))  # reward/done deliberately do not determine our outcome
        emit(index, env.sim.get_state().flatten().tolist())


class CanReplayAdapter:
    def describe(self):
        policy = requirements('view_recorded_states')
        policy.update(execution_mode='closed_loop_simulation',
            profile_fields=['components','state_schema','action_schema','frames','units','time_model'])
        policy['required_rights']['data'] = 'evaluate'
        return {'adapter_id': PROFILE, 'version': '1.0.0', 'contract_version': '1.0.0',
            'profile': {'id': PROFILE, 'domain': 'manipulation', 'state': 'time + 37 qpos + 33 qvel from exact XML',
                'action': 'original OSC_POSE at 20 Hz, world input frame', 'observation': 'headless simulator state',
                'outcome': 'independent Can target-bin and gripper-separation geometry',
                'operations': [OPERATION], 'execution': 'closed_loop_simulation', 'data_license': 'MIT pinned source', 'code_license': 'MIT'},
            'license': 'MIT', 'execution_mode': 'closed_loop_simulation',
            'source_contract': {'kind': 'can_replay_v1', 'operations': {OPERATION: policy}},
            'environment_lock': {'runtime': 'robosuite==1.5.1; mujoco==3.2.7; numpy==2.2.6; numba==0.61.2; mink==0.0.13'},
            'limitations': ['Selected simulator version is pinned; original MuJoCo version remains unknown',
                'No physical robot replay; no source labels or recorded states are upgraded',
                'Optional runtime required; images, video and device calls disabled'],
            'request_schemas': {OPERATION: obj({'episode_id': ID}, ['episode_id'])}}

    def resources(self, service):
        return [{'kind': 'episode', **item, 'profile': PROFILE,
            'prepare_request': {'profile': PROFILE, 'operation': OPERATION, 'episode_id': item['id'],
                'max_seconds': 180, 'resource_limits': {'cpu_seconds': 150, 'memory_bytes': 2147483648, 'artifact_bytes': 10485760},
                'required_runner_capabilities': ['cpu_seconds','memory_bytes','wall_seconds','artifact_bytes'],
                'verification': {'mode': 'metric_recompute'}}}
            for item in service.corpus.listing()['episodes'] if item['domain'] == 'manipulation']

    def snapshot(self, service, request):
        runtime = runtime_pin(); manifest, env, version = load()
        item = service._episode(request['episode_id'], 'manipulation'); episode = item['episode']
        demo = manifest['demos'].get(episode['source_key'])
        if (item['source'].get('sha256') != manifest['source_sha256']
                or not demo or len(episode['states']) != demo['states_shape'][0]
                or len(episode['actions']) != demo['actions_shape'][0]
                or not 1 <= len(episode['actions']) <= 1000
                or any(len(s) != 71 for s in episode['states'])
                or any(len(a) != 7 for a in episode['actions'])):
            raise Fault('PROFILE_INCOMPATIBLE', 'Episode dimensions differ from Can source metadata')
        if demo['ep_meta_present'] or demo['ep_meta'] is not None:
            raise Fault('PROFILE_INCOMPATIBLE', 'This version supports the pinned fixed Can task with absent ep_meta')
        _, mapping = relocated_xml(demo['model_file'])
        # Environment and source hashes are preserved in the imported source contract as well.
        if episode['environment'] != env:
            raise Fault('PROFILE_INCOMPATIBLE', 'Episode environment differs from the original supplement')
        return {'episode_id': item['id'], 'payload_sha256': item['payload_sha256'],
            'source_sha256': manifest['source_sha256'], 'source_provenance_sha256': digest(item['source']),
            'source_key': episode['source_key'], 'group_id': episode['group_id'], 'split': item['split'],
            'supplement_sha256': version, 'model_file': demo['model_file'],
            'model_sha256': demo['model_sha256'], 'env_args_sha256': manifest['env_args_sha256'],
            'initial_state_sha256': digest(episode['states'][0]), 'actions_sha256': digest(episode['actions']),
            'action_count': len(episode['actions']), 'runtime': runtime, 'assets': mapping,
            'execution': 'one_initial_state_then_recorded_actions', 'controller_rate_hz': 20,
            'controller_input_frame': 'world', 'source_ep_meta': None, 'original_mujoco_version': None,
            'allocation_override': ALLOCATION}

    def execute(self, service, plan, records, artifacts, check, artifact):
        # No simulator imports occur during discovery or metadata preparation.
        os.environ['NUMBA_DISABLE_JIT'] = '1'
        for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
            os.environ[key] = '1'
        import robosuite
        manifest, env_args, _ = load(); snapshot = plan['snapshot']
        episode = service._episode(snapshot['episode_id'], 'manipulation')['episode']
        xml, mapping = relocated_xml(snapshot['model_file']); env = None; stage = 'make_environment'
        trace = []; batch = []
        def emit(index, state):
            if any(warning.number for warning in env.sim.data._data.warning):
                raise Fault('EXECUTION_FAILED', 'MuJoCo warning: replay cannot retain truncated or unstable dynamics')
            check(); entry = {'step': index, 'state': state}; trace.append(entry); batch.append(entry)
            if len(batch) == 32:
                artifact('can-partial-%04d.json' % index, encoded(batch).encode()); batch.clear()
        try:
            check()
            env = robosuite.make(env_args['env_name'], **deepcopy(env_args['env_kwargs']))
            stage = 'reset_controller_state'
            # The exact source XML follows immediately; a second random model allocation
            # is unnecessary. Soft reset still resets controllers and observables.
            env.hard_reset = False
            env.set_ep_meta({})  # API initialization, not an invented source ep_meta value.
            env.reset()
            stage = 'load_exact_source_xml'
            env.reset_from_xml_string(xml)
            env.sim.reset()
            stage = 'confirm_compiled_layout'
            compiled = confirm_layout(env.sim.model._model, manifest['models'][snapshot['model_file']])
            stage = 'execute_recorded_actions'
            advance_actions(env, episode['states'][0], episode['actions'], check, emit)
            artifact('can-action-trace.json', encoded({'trace': trace,
                'action_count': len(episode['actions']), 'actions_sha256': snapshot['actions_sha256'],
                'state_restorations': 1, 'simulation_action_replay': True, 'physical_replay': False,
                'compiled_layout': compiled, 'runtime': snapshot['runtime'],
                'asset_mapping': mapping, 'initial_state_sha256': snapshot['initial_state_sha256'],
                'source_model_sha256': snapshot['model_sha256'], 'controller_rate_hz': 20,
                'controller_input_frame': 'world', 'allocation_override': ALLOCATION,
                'maxuse_arena_bytes': int(env.sim.data._data.maxuse_arena),
                'engine_warning_count': sum(w.number for w in env.sim.data._data.warning)}).encode())
            records.append({'simulation_action_replay': True, 'task_outcome': 'pending_independent_check'})
        except Exception as exc:
            if not isinstance(exc, Fault):
                import traceback
                artifact('can-runtime-error.json', encoded({'type': type(exc).__name__, 'stage': stage,
                    'message': str(exc)[:2000], 'traceback': traceback.format_exc()[-6000:]}).encode())
                if 'Could not allocate memory' in str(exc):
                    raise Fault('BUDGET_EXCEEDED', 'MuJoCo could not allocate within the enforced memory ceiling') from exc
            raise
        finally:
            if env is not None: env.close()
