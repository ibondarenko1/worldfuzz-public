"""Pinned headless adaptation of Pymunk 7.2.0's published basic_test task."""
import random

from ..contracts import Fault


def run_basic(seed, parameters, check):
    import pymunk
    if pymunk.version!='7.2.0' or pymunk.chipmunk_version!='2.0.1-ade7ed72849e60289eefb7a41e79ae6322fefaf3':
        raise Fault('PLAN_CHANGED','Pinned Pymunk or Munk2D version is unavailable')
    space=pymunk.Space();space.gravity=tuple(parameters['gravity_px_per_s2']);rng=random.Random(seed)
    body=shape=None;trace=[];removed_tick=None
    for tick in range(parameters['steps']):
        if tick%128==0:check()
        if tick==parameters['spawn_tick']:
            inertia=pymunk.moment_for_circle(parameters['mass'],0,parameters['radius_px'],(0,0))
            body=pymunk.Body(parameters['mass'],inertia);body.position=(rng.randint(parameters['x_min_px'],parameters['x_max_px']),parameters['initial_y_px'])
            shape=pymunk.Circle(body,parameters['radius_px'],(0,0));space.add(body,shape)
        if body is not None and body.position.y<parameters['remove_below_y_px']:
            space.remove(shape,body);removed_tick=tick;body=shape=None
        if body is not None:
            trace.append({'tick':tick,'position':[body.position.x,body.position.y],
                          'velocity':[body.velocity.x,body.velocity.y]})
        space.step(parameters['dt_seconds'])
    return {'seed':seed,'steps':parameters['steps'],'trace':trace,'removed_tick':removed_tick,
        'engine':{'name':'pymunk','version':pymunk.version,'native_name':'Munk2D','native_version':pymunk.chipmunk_version},
        'simulation_executed':True,'device_calls':0,'task_outcome':'unverified'}
