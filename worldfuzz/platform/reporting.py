"""One evidence interpretation for UI, CI and partner integrations."""
from xml.etree import ElementTree as ET


def result_report(service, result_id):
    checked = service.get_result(result_id)
    body = checked['result']
    plan = service._plan(checked['plan_id'])
    report = body.get('verification')
    state = body['execution_status']
    outcome = report['proven_result'] if report else 'not_evaluated'
    expected = plan['verification_contract']['mode']
    checks = report['performed_checks'] if report else []
    performed = any(c['mode'] == expected and c['status'] == 'passed' for c in checks)
    if state == 'failed':
        status, code = 'error', 1
    elif state != 'completed' or not performed:
        status, code = 'incomplete', 3
    elif outcome == 'violation_found':
        status, code = 'violation', 2
    else:
        status, code = 'passed', 0
    # Report performed records, not an inferred ordinal or unseen user intent.
    resources = ([{'resource_id':f"movingai:{r['case_index']}",'case_index':r['case_index']}
                  for r in body.get('records',[]) if 'case_index' in r]
                 if plan['request']['operation']=='solve_navigation' else [])
    dataset_export = None
    meaning = 'CI pass means the requested checks passed within their stated scope; it does not certify a robot or flight.'
    if plan['request']['operation']=='solve_navigation':
        selected=', '.join(f"{r['resource_id']}={r['case_index']}" for r in resources) or 'none recorded'
        meaning = f'Moving AI resource=case_index (zero-based): {selected}. Checks cover recorded grid results only; no physical replay.'
    if plan['request']['operation']=='export_dataset':
        required = {'episodes.jsonl','dataset-manifest.json'}
        # get_result above checks the current bytes and hashes of every artifact.
        artifacts = [a for a in body.get('artifacts',[]) if a['name'] in required]
        confirmed = state=='completed' and required.issubset(a['name'] for a in artifacts)
        if state=='completed' and not confirmed:
            status, code = 'incomplete', 3
        dataset_export = {'confirmed':confirmed,'artifacts':artifacts,
            'reason':'Completed export with existing hash-checked payload and manifest' if confirmed else
                     'Export is not confirmed: completed execution and both payload and manifest are required'}
        meaning = ('Dataset export confirmed: completed execution; episodes.jsonl and dataset-manifest.json exist and pass hash checks.'
                   if confirmed else dataset_export['reason']) + ' Scope: stored data; no physical replay.'
    summary = {
        'schema_version': '1.0.0', 'result_id': result_id,
        'result_sha256': checked['result_sha256'],
        'profile': plan['request']['profile'], 'operation': plan['request']['operation'],
        'execution_status': state, 'task_outcome': outcome,
        'ci_status': status, 'exit_code': code, 'required_check': expected,
        'performed_checks': checks,
        'verification_scope': report['scope'] if report else 'No verifier receipt is available',
        'physics_replayed': bool(report and report['physics_replayed']),
        'recording': True,
        'meaning': meaning,
    }
    suite = ET.Element('testsuite', name='WorldFuzz', tests='1',
                       failures=str(int(status == 'violation')),
                       errors=str(int(status == 'error')),
                       skipped=str(int(status == 'incomplete')))
    case = ET.SubElement(suite, 'testcase', name=summary['operation'] + ':' + expected,
                         classname=summary['profile'])
    properties = ET.SubElement(case, 'properties')
    for name in ('result_id', 'result_sha256', 'execution_status', 'task_outcome',
                 'ci_status', 'verification_scope'):
        ET.SubElement(properties, 'property', name=name, value=summary[name])
    for resource in resources:
        ET.SubElement(properties, 'property', name='resource_id', value=resource['resource_id'])
        ET.SubElement(properties, 'property', name='case_index', value=str(resource['case_index']))
    if dataset_export is not None:
        ET.SubElement(properties, 'property', name='dataset_export_confirmed', value=str(dataset_export['confirmed']).lower())
    if status == 'violation':
        ET.SubElement(case, 'failure', type='robotics_requirement_violation',
                      message='The preserved verifier found a requirement violation')
    elif status == 'error':
        ET.SubElement(case, 'error', type='execution_error',
                      message='Execution failed; this is not a robotics regression verdict')
    elif status == 'incomplete':
        ET.SubElement(case, 'skipped', message='Execution or requested verification is incomplete')
    ET.SubElement(case, 'system-out').text = summary['meaning']
    return {'summary': summary, 'junit_xml': ET.tostring(suite, encoding='unicode')}
