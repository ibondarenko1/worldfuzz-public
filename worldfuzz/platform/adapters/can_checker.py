"""Independent PickPlaceCan kinematics and outcome. No robosuite reward/done calls."""
import json
import math

from ...corpus import digest
from ..contracts import Fault
from .can_metadata import ALLOCATION, load, relocated_xml, confirm_layout

PROTOCOL = 'pickplacecan.geometry.v1'


def geometry(object_position, bin_position, half_size, gripper_position):
    values = [*object_position, *bin_position, *half_size, *gripper_position]
    if not all(math.isfinite(v) for v in values):
        raise Fault('VERIFICATION_FAILED', 'Non-finite Can geometry')
    # Can is object index 3: positive x/y quadrant, strict original center bounds.
    inside = (bin_position[0] < object_position[0] < bin_position[0] + half_size[0]
        and bin_position[1] < object_position[1] < bin_position[1] + half_size[1]
        and bin_position[2] < object_position[2] < bin_position[2] + 0.1)
    distance = math.dist(object_position, gripper_position)
    separated = 1.0 - math.tanh(10.0 * distance) < 0.6
    return {'object_in_target_compartment': inside, 'gripper_separated': separated,
            'gripper_distance_m': distance, 'success': inside and separated,
            'object_position_m': object_position, 'bin_position_m': bin_position,
            'compartment_extent_xy_m': half_size, 'gripper_position_m': gripper_position}


def verify(service, run_id, plan, artifacts, checkpoint):
    import mujoco
    import numpy as np
    raw = service.artifact(run_id, 'can-action-trace.json', result={'artifacts': artifacts})
    observed = json.loads(raw); snapshot = plan['snapshot']
    episode = service._episode(snapshot['episode_id'], 'manipulation')['episode']
    manifest, _, _ = load(); xml, mapping = relocated_xml(snapshot['model_file'])
    model = mujoco.MjModel.from_xml_string(xml)
    compiled = confirm_layout(model, manifest['models'][snapshot['model_file']])
    trace = observed['trace']; count = len(episode['actions'])
    if (len(trace) != count + 1 or observed['action_count'] != count or observed['state_restorations'] != 1
            or observed['actions_sha256'] != digest(episode['actions']) or observed['runtime'] != snapshot['runtime']
            or observed['compiled_layout'] != compiled or observed['asset_mapping'] != mapping
            or observed['initial_state_sha256'] != digest(episode['states'][0])
            or observed['source_model_sha256'] != snapshot['model_sha256']
            or observed['simulation_action_replay'] is not True or observed['physical_replay'] is not False
            or observed['controller_rate_hz'] != 20 or observed['controller_input_frame'] != 'world'
            or observed['allocation_override'] != ALLOCATION or observed['engine_warning_count'] != 0
            or not 0 < observed['maxuse_arena_bytes'] <= model.narena
            or trace[0]['state'] != episode['states'][0]):
        raise Fault('VERIFICATION_FAILED', 'Can replay trace provenance or initialization differs')
    # Only kinematics is recomputed here. No actions are re-executed and no source rewards are read.
    data = mujoco.MjData(model)
    body = model.body('Can_main').id; bin_id = model.body('bin2').id
    site = model.site('gripper0_right_grip_site').id
    floor = int(model.body_geomadr[bin_id]); half = model.geom_size[floor][:2].tolist()
    if int(model.geom_type[floor]) != int(mujoco.mjtGeom.mjGEOM_BOX):
        raise Fault('VERIFICATION_FAILED', 'Target bin floor geometry is unsupported')
    # Published BinsArena task sizes are 0.39 x 0.49, whereas XML floor is
    # 0.40 x 0.50. Pin that task convention rather than silently widening it.
    if half != [0.2, 0.25]:raise Fault('VERIFICATION_FAILED', 'Unsupported Can bin dimensions')
    half = [0.195, 0.245]
    results = []
    for index, item in enumerate(trace):
        checkpoint(); state = item['state']
        if (item['step'] != index or len(state) != 71 or not all(math.isfinite(v) for v in state)
                or not math.isclose(state[0], trace[0]['state'][0] + index / 20, abs_tol=1e-8)):
            raise Fault('VERIFICATION_FAILED', 'Can trace dimensions or simulated time differs')
        data.time = state[0]; data.qpos[:] = state[1:38]; data.qvel[:] = state[38:71]
        mujoco.mj_forward(model, data)
        results.append(geometry(data.xpos[body].tolist(), data.xpos[bin_id].tolist(), half,
                                data.site_xpos[site].tolist()))
    final = results[-1]
    return {'episode_id': snapshot['episode_id'], 'source_key': snapshot['source_key'],
        'group_id': snapshot['group_id'], 'split': snapshot['split'], 'steps': count,
        'simulation_action_replay': True, 'physics_replayed': False,
        'task_outcome_independently_verified': True, 'task_outcome': 'success' if final['success'] else 'failure',
        'outcome': 'success' if final['success'] else 'failure', 'final_geometry': final,
        'first_success_step': next((i for i, value in enumerate(results) if value['success']), None),
        'source_label': episode['label'], 'source_label_used_by_checker': False,
        'protocol': PROTOCOL, 'evidence': 'executed recorded actions; independent final-state kinematics and geometry',
        'original_mujoco_version': None, 'physical_calibration': None}
