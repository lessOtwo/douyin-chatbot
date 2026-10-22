"""Targeted local startup checks, without upstream test suites or real message sending."""
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

root = Path(__file__).resolve().parents[1]
result = subprocess.run(
    ['docker', 'compose', '-f', str(root/'compose.yaml'), '--env-file', str(root/'.env'),
     'ps', '--all', '--format', 'json'],
    capture_output=True, text=True, encoding='utf-8', check=True,
)
raw = result.stdout.strip()
containers = json.loads(raw) if raw.startswith('[') else [json.loads(line) for line in raw.splitlines()]
states = [{key: row.get(key) for key in ['Service', 'State', 'Health', 'ExitCode']} for row in containers]
expected = {'business-service','postgres','redis','vector','mongo','minio','fastgpt','fastgpt-plugin','fastgpt-code-sandbox','aiproxy'}
assert expected <= {row['Service'] for row in states}, states
assert not any(row['Service'].startswith('chatwoot') or row['Service']=='mailpit' for row in states), states
for row in states:
    if row['Service'] == 'mongo-init':
        assert row['State'] == 'exited' and row['ExitCode'] == 0, row
    else:
        assert row['State'] == 'running' and row['Health'] in ('', 'healthy', None), row

pages = []
for name, url, expected in [
    ('business', 'http://127.0.0.1:8088/health', 200),
    ('fastgpt', 'http://127.0.0.1:3000', 200),
]:
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    assert status == expected, (name, status, expected)
    pages.append({'name': name, 'status': status, 'passed': True})
env_values = {}
for line in (root/'.env').read_text(encoding='utf-8').splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        env_values[key] = value
request = urllib.request.Request('http://127.0.0.1:8088/api/snapshot',
                                 headers={'x-api-key': env_values['BUSINESS_API_KEY']})
with urllib.request.urlopen(request, timeout=15) as response:
    snapshot = json.load(response)
snapshot_counts = {key: snapshot.get('totals', {}).get(key) for key in
                   ('conversations', 'events', 'outbox')}
assert all(isinstance(value, int) for value in snapshot_counts.values()), snapshot_counts
smoke = json.loads((root/'artifacts/fastgpt-workflow-smoke.json').read_text(encoding='utf-8'))
record = {'result': 'passed', 'containers': states, 'pages': pages,
          'business_snapshot_counts': snapshot_counts,
          'real_engine_flow_verified': smoke['result'] == 'passed',
          'real_model_verified': smoke['result'] == 'passed',
          'real_model_scope': smoke['scope'],
          'real_channel_verified': False, 'production_deployed': False, 'full_tests_run': False}
(root/'artifacts').mkdir(exist_ok=True)
(root/'artifacts/runtime-checks.json').write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
print(json.dumps(record))
