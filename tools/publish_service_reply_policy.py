"""Publish the merchant script categories in the existing workflow."""
import argparse
import copy
import hashlib
import http.cookiejar
import json
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request

from build_fastgpt_workflow import env_password


ROOT = Path(__file__).resolve().parents[1]
APP_ID = '6ad7276957c1257e1bb5ed75'
BASELINE = '50c8b1a'
AI_NODES = ('lead_received_reply', 'contact_reply', 'retry_reply', 'normal_reply')


def previous(path):
    return subprocess.check_output(['git', 'show', f'{BASELINE}:{path}'],
                                   cwd=ROOT, encoding='utf-8')


def node_inputs(flow):
    return {node['nodeId']: next(item for item in node['inputs']
            if item['key'] == 'systemPrompt') for node in flow['nodes']
            if node['nodeId'] in AI_NODES}


def replace_once(value, old, new, label):
    if value.count(old) != 1:
        raise RuntimeError(f'Published {label} differs from the expected wording (count={value.count(old)})')
    return value.replace(old, new, 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:3000/fastgpt')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(path, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        with opener.open(urllib.request.Request(
            args.base_url.rstrip('/') + path, data=data,
            headers={'Content-Type': 'application/json', 'fastgpt-web-request': '1'}),
            timeout=60) as response:
            result = json.load(response)
        if result.get('code') != 200:
            raise RuntimeError(f'FastGPT rejected {path}: {result.get("msg")}')
        return result.get('data')

    pre = request('/api/support/user/account/preLogin?username=root')
    request('/api/support/user/account/loginByPassword', {
        'username': 'root',
        'password': hashlib.sha256(env_password().encode('utf-8')).hexdigest(),
        'code': pre['code'], 'language': 'zh-CN'})
    query = urllib.parse.urlencode({'appId': APP_ID})
    app = request('/api/core/app/detail?' + query)
    version = app.get('publishedVersionId')
    if not version:
        raise RuntimeError('Workflow has no published version')
    published = request('/api/core/app/version/detail?' + urllib.parse.urlencode({
        'appId': APP_ID, 'versionId': version}))
    for key in ('nodes', 'edges', 'chatConfig'):
        if app[key] != published[key]:
            raise RuntimeError(f'Unpublished {key} changes exist; refusing to overwrite')

    old_flow = json.loads(previous('config/fastgpt-lead-workflow.json'))
    new_flow = json.loads((ROOT / 'config/fastgpt-lead-workflow.json').read_text(encoding='utf-8'))
    signature = lambda flow: sorted((edge['source'], edge['target'], edge['sourceHandle'])
                                    for edge in flow['edges'])
    if (set(node['nodeId'] for node in published['nodes']) !=
            set(node['nodeId'] for node in old_flow['nodes']) or
            signature(published) != signature(old_flow) or
            signature(old_flow) != signature(new_flow)):
        raise RuntimeError('Published workflow topology differs from the source')
    before_parts = previous('config/fastgpt-system-prompt.md').split('\n\n')
    after_parts = (ROOT / 'config/fastgpt-system-prompt.md').read_text(
        encoding='utf-8').split('\n\n')
    if len(before_parts) != len(after_parts):
        raise RuntimeError('System prompt paragraph structure changed')
    changes = [(old, new) for old, new in zip(before_parts, after_parts) if old != new]
    if not changes:
        raise RuntimeError('System prompt has no changes to publish')

    target = copy.deepcopy(published)
    live_prompts = node_inputs(target)
    old_prompts = node_inputs(old_flow)
    new_prompts = node_inputs(new_flow)
    for node_id in AI_NODES:
        value = live_prompts[node_id]['value']
        for index, (old, new) in enumerate(changes, 1):
            if old.startswith('fields 为对象') and old not in value:
                legacy = '询问类别、预约时间或购买数量时'
                if value.count(legacy) not in (1, 2):
                    raise RuntimeError(f'Published {node_id} legacy field instruction differs')
                value = value.replace(legacy,
                    '询问服务目标细分、预约时间、批发数量、零售型号或档次时', 1)
            else:
                value = replace_once(value, old, new, f'{node_id} paragraph {index}')
        if node_id in ('contact_reply', 'retry_reply', 'normal_reply'):
            old_extra = old_prompts[node_id]['value'].rsplit('\n', 1)[-1]
            new_extra = new_prompts[node_id]['value'].rsplit('\n', 1)[-1]
            if old_extra != new_extra:
                value = replace_once(value, old_extra, new_extra, f'{node_id} branch instruction')
        live_prompts[node_id]['value'] = value

    print(f'Ready: {version}; changed {len(AI_NODES)} AI prompt nodes')
    if not args.apply:
        return
    backup = ROOT / f'artifacts/fastgpt-service-reply-policy-backup-{version}.json'
    if backup.exists():
        raise RuntimeError(f'Backup already exists: {backup}')
    backup.write_text(json.dumps({'previous_version_id': version,
                                  'previous_workflow': published}, ensure_ascii=False,
                                 indent=2) + '\n', encoding='utf-8')
    payload = {key: target[key] for key in ('nodes', 'edges', 'chatConfig')}
    request('/api/core/app/version/publish?' + query, {
        **payload, 'isPublish': True, 'versionName': '商家话术类别'})
    new_version = request('/api/core/app/detail?' + query).get('publishedVersionId')
    if not new_version or new_version == version:
        raise RuntimeError('Published version did not change')
    actual = request('/api/core/app/version/detail?' + urllib.parse.urlencode({
        'appId': APP_ID, 'versionId': new_version}))
    if any(actual[key] != payload[key] for key in payload):
        raise RuntimeError('Published workflow differs from requested changes')
    print(f'Verified published version {new_version}; backup: {backup}')


if __name__ == '__main__':
    main()
