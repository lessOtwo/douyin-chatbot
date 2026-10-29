"""Prepare and publish the compact private-message router with scoped checks."""

import argparse
import copy
import hashlib
import http.cookiejar
import json
from pathlib import Path
import urllib.parse
import urllib.request
import uuid

from build_fastgpt_workflow import env_password
from workflow_dialogue import (ALTERNATE_WASH_EXAMPLE, COMMON_CHANGES,
                               reshape_flow, rewrite_common_prompt)


ROOT = Path(__file__).resolve().parents[1]
APP_ID = '6ad7276957c1257e1bb5ed75'


def prepare_local():
    prompt_path = ROOT / 'config/fastgpt-system-prompt.md'
    prompt = prompt_path.read_text(encoding='utf-8')
    rewritten = rewrite_common_prompt(prompt)
    if rewritten != prompt:
        prompt_path.write_text(rewritten, encoding='utf-8')
    flow_path = ROOT / 'config/fastgpt-lead-workflow.json'
    original = json.loads(flow_path.read_text(encoding='utf-8'))
    updated = reshape_flow(copy.deepcopy(original))
    if updated != original:
        flow_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    prd_path = ROOT / 'docs/PRD.md'
    prd = prd_path.read_text(encoding='utf-8')
    for old, new in (
        ('服务型直接询问预约时间', '服务型直接询问预计到店或上门时间'),
        ('服务型、批发型则分别以该预约时间是否有空闲工作人员可接待或上门、折扣需门店或工作人员沟通为由邀请留资',
         '服务型、批发型则分别以计划到店或上门的时间如何确认（洗车等即办服务只说忙不忙或是否要等位）、折扣需门店或工作人员沟通为由邀请留资'),
        ('FastGPT 每轮生成答疑和与当前问题相关的留资邀请，业务服务按会话状态决定是否附在末尾',
         'FastGPT 每轮生成答疑；允许邀请且上一轮未索要过联系方式时，可生成与当前问题相关的留资邀请，业务服务决定是否附在末尾'),
        ('尚未留资、未明确答应稍后提供且不是同行时，模型生成与答疑相连的“留个联系方式”邀请，由业务服务判断是否发送',
         '尚未留资、未明确答应稍后提供、上一轮未索要过联系方式且不是同行时，模型可生成与答疑相连的“留个联系方式”邀请，由业务服务判断是否发送'),
    ):
        if old in prd:
            prd = prd.replace(old, new, 1)
        elif new not in prd:
            raise ValueError(f'PRD wording not found: {old[:30]}')
    if prd != prd_path.read_text(encoding='utf-8'):
        prd_path.write_text(prd, encoding='utf-8')
    print('Prepared local prompt and workflow: 5 nodes, 4 edges')


def client(base):
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(path, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        with opener.open(urllib.request.Request(
                base.rstrip('/') + path, data=data,
                headers={'Content-Type': 'application/json', 'fastgpt-web-request': '1'}),
                timeout=120) as response:
            result = json.load(response)
        if result.get('code') != 200:
            raise RuntimeError(f'FastGPT rejected {path}: {result.get("msg")}')
        return result.get('data')

    pre = request('/api/support/user/account/preLogin?username=root')
    request('/api/support/user/account/loginByPassword', {
        'username': 'root',
        'password': hashlib.sha256(env_password().encode('utf-8')).hexdigest(),
        'code': pre['code'], 'language': 'zh-CN'})
    return opener, request


def check_case(opener, base, app, flow, message, variables):
    payload = {
        'appId': APP_ID, 'appName': app['name'], 'chatId': 'compact-check-' + uuid.uuid4().hex,
        'messages': [{'role': 'user', 'content': message}],
        'nodes': flow['nodes'], 'edges': flow['edges'], 'chatConfig': flow['chatConfig'],
        'variables': variables,
    }
    with opener.open(urllib.request.Request(
            base.rstrip('/') + '/api/core/chat/chatTest',
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'fastgpt-web-request': '1'}),
            timeout=120) as response:
        raw = response.read().decode('utf-8', 'replace')
    parts = []
    for block in raw.replace('\r\n', '\n').split('\n\n'):
        if not (block.startswith('event: answer\n') or block.startswith('event: fastAnswer\n')):
            continue
        for line in block.splitlines():
            if line.startswith('data: ') and line[6:] != '[DONE]':
                event = json.loads(line[6:])
                content = event.get('choices', [{}])[0].get('delta', {}).get('content')
                if isinstance(content, str):
                    parts.append(content)
    return json.loads(''.join(parts))


def focused_checks(opener, base, app, flow):
    profile = {
        'fields': {'所属行业': '汽车服务', '完整门店名称': '测试洗车店',
                   '服务区域': '泰安市', '话术类别': '到店/上门服务型',
                   '门店详细地址': '测试路1号', '门店电话可公开': '是',
                   '门店联系电话': '0538-1234567'},
        'projects': {'main': [{'name': '洗车', 'question': '您想什么时候到店洗车？'}],
                     'available': [], 'excluded': []},
    }
    variables = {'merchant_id': 'compact-check', 'industry': '汽车服务',
                 'project_selection_question': '您想咨询洗车吗？',
                 'merchant_profile': json.dumps(profile, ensure_ascii=False),
                 'last_contact_request': 'false', 'contact_request_allowed': 'true'}
    time_reply = check_case(opener, base, app, flow, '我想周日下午3点到店洗车', variables)
    assert time_reply['action'] == 'reply' and all(
        term not in time_reply['reply'] for term in ('档期', '申请', '已预约')), time_reply
    address = check_case(opener, base, app, flow, '你们门店的详细地址是什么？', {
        **variables, 'last_contact_request': 'true', 'contact_request_allowed': 'false'})
    assert '测试路1号' in address['reply'] and not address.get('contact_request') and '留个联系方式' not in address['reply'], address
    merchant_contact = check_case(opener, base, app, flow, '你们门店的联系电话是多少？', {
        **variables, 'contact_request_allowed': 'false'})
    assert '0538-1234567' in merchant_contact['reply'], merchant_contact
    provided = check_case(opener, base, app, flow, '我的微信是abc12345', variables)
    assert provided['fields'].get('wechat') == 'abc12345', provided
    peer = check_case(opener, base, app, flow, '我也开洗车店，想向你们推销洗车液', variables)
    assert peer['action'] == 'peer', peer
    print('Focused draft checks passed: wash time, address cooldown, merchant contact, lead, peer')


def verify_live(base):
    settings = dict(line.split('=', 1) for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines()
                    if '=' in line and not line.startswith('#'))
    key = settings.get('FASTGPT_APP_KEY', '')
    if not key:
        raise RuntimeError('FASTGPT_APP_KEY is missing')
    profile = {
        'fields': {'所属行业': '汽车服务', '完整门店名称': '测试洗车店',
                   '服务区域': '泰安市', '话术类别': '到店/上门服务型',
                   '门店详细地址': '测试路1号'},
        'projects': {'main': [{'name': '洗车', 'question': '您想什么时候到店洗车？'}],
                     'available': [], 'excluded': []},
    }
    base_variables = {'merchant_id': 'compact-live-check', 'industry': '汽车服务',
                      'project_selection_question': '您想咨询洗车吗？',
                      'merchant_profile': json.dumps(profile, ensure_ascii=False)}

    def ask(message, chat_id=None, **variables):
        body = {'appId': APP_ID, 'chatId': chat_id or 'compact-live-' + uuid.uuid4().hex,
                'stream': False, 'messages': [{'role': 'user', 'content': message}],
                'variables': {**base_variables, **variables}}
        with urllib.request.urlopen(urllib.request.Request(
                base.rstrip('/') + '/api/v1/chat/completions',
                data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}),
                timeout=120) as response:
            raw = json.load(response)
        return json.loads(raw['choices'][0]['message']['content'])

    time_reply = ask('周日下午3点想来洗车', last_contact_request='false',
                     contact_request_allowed='true')
    assert time_reply['action'] == 'reply' and all(
        word not in time_reply['reply'] for word in ('档期', '申请', '已预约')), time_reply
    address = ask('你们门店详细地址在哪里？', last_contact_request='true',
                  contact_request_allowed='false')
    assert '测试路1号' in address['reply'] and not address.get('contact_request'), address
    chat_id = 'compact-history-' + uuid.uuid4().hex
    first = ask('我想在你们店洗个车', chat_id=chat_id,
                last_contact_request='false', contact_request_allowed='true')
    assert first['action'] == 'reply' and not first.get('contact_request'), first
    second = ask('这周日下午3点', chat_id=chat_id,
                 last_contact_request='false', contact_request_allowed='true')
    assert second['action'] == 'reply' and all(
        word not in second['reply'] for word in ('档期', '申请', '已预约')), second
    third = ask('你们地址在哪？', chat_id=chat_id,
                last_contact_request='true', contact_request_allowed='false')
    assert '测试路1号' in third['reply'] and not third.get('contact_request'), third
    print('Published API checks passed: wash wording, address cooldown, and three-turn history')


def publish(base, expected_version, apply):
    opener, request = client(base)
    query = urllib.parse.urlencode({'appId': APP_ID})
    app = request('/api/core/app/detail?' + query)
    version = app.get('publishedVersionId')
    if version != expected_version:
        raise RuntimeError(f'Published version changed: {version}, expected {expected_version}')
    published = request('/api/core/app/version/detail?' + urllib.parse.urlencode({
        'appId': APP_ID, 'versionId': version}))
    if any(app[key] != published[key] for key in ('nodes', 'edges', 'chatConfig')):
        draft_nodes = {node['nodeId']: node for node in app['nodes']}
        published_nodes = {node['nodeId']: node for node in published['nodes']}
        changes = {
            'added_nodes': sorted(set(draft_nodes) - set(published_nodes)),
            'removed_nodes': sorted(set(published_nodes) - set(draft_nodes)),
            'changed_nodes': {node_id: sorted(key for key in set(draft_nodes[node_id]) | set(published_nodes[node_id])
                                              if draft_nodes[node_id].get(key) != published_nodes[node_id].get(key))
                              for node_id in sorted(set(draft_nodes) & set(published_nodes))
                              if draft_nodes[node_id] != published_nodes[node_id]},
            'edges_changed': app['edges'] != published['edges'],
            'chat_config_changed': app['chatConfig'] != published['chatConfig'],
        }
        print('Online draft differs from published version: ' + json.dumps(changes, ensure_ascii=True))
        allowed_sentence = ('成人学历提升的新生学习形式按现行规则称“脱产”或“非脱产”；'
                            '自 2025 年秋季起不再把“函授”“业余”当作当前新生的学习形式，'
                            '具体安排以当年当地招生简章为准。')
        allowed = not changes['added_nodes'] and not changes['removed_nodes']
        allowed = allowed and not changes['edges_changed'] and not changes['chat_config_changed']
        for node_id, keys in changes['changed_nodes'].items():
            if node_id == 'lead_received_reply':
                allowed = allowed and set(keys) <= {'position', 'inputs'}
                before = {item['key']: item.get('value') for item in published_nodes[node_id]['inputs']}
                after = {item['key']: item.get('value') for item in draft_nodes[node_id]['inputs']}
                expected_prompt = before.get('systemPrompt', '').replace(allowed_sentence, '', 1)
                expected_prompt = expected_prompt.replace(COMMON_CHANGES[-1][0], ALTERNATE_WASH_EXAMPLE, 1)
                allowed = allowed and expected_prompt == after.get('systemPrompt')
                allowed = allowed and all(before.get(key) == after.get(key) for key in set(before) | set(after) if key != 'systemPrompt')
            else:
                allowed = allowed and keys == ['position']
        if not allowed:
            raise RuntimeError('Unrecognized unpublished draft changes; refusing to publish')
        print('Preserving current draft node positions and the existing lead-confirmation prompt edit')
    target = reshape_flow(copy.deepcopy(app))
    assert len(target['nodes']) == 5 and len(target['edges']) == 4
    focused_checks(opener, base, app, target)
    if not apply:
        print(f'Ready to publish over {version}; no online changes made')
        return
    backup = ROOT / f'artifacts/fastgpt-before-compact-{version}.json'
    if backup.exists():
        raise RuntimeError(f'Backup already exists: {backup}')
    backup.parent.mkdir(parents=True, exist_ok=True)
    current_draft = request('/api/core/app/detail?' + query)
    if current_draft['publishedVersionId'] != version or any(
            current_draft[key] != app[key] for key in ('nodes', 'edges', 'chatConfig')):
        raise RuntimeError('Online draft changed during checks; refusing to publish')
    backup.write_text(json.dumps({'previous_version_id': version,
                                  'previous_workflow': published,
                                  'previous_draft': app},
                                 ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    payload = {key: target[key] for key in ('nodes', 'edges', 'chatConfig')}
    request('/api/core/app/version/publish?' + query, {
        **payload, 'isPublish': True, 'versionName': '简化分流与洗车到店话术'})
    new_version = request('/api/core/app/detail?' + query).get('publishedVersionId')
    if not new_version or new_version == version:
        raise RuntimeError('Published version did not change')
    actual = request('/api/core/app/version/detail?' + urllib.parse.urlencode({
        'appId': APP_ID, 'versionId': new_version}))
    if any(actual[key] != payload[key] for key in payload):
        raise RuntimeError('Published content differs from prepared workflow')
    print(f'Published and verified {new_version}; backup: {backup}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-local', action='store_true')
    parser.add_argument('--base-url')
    parser.add_argument('--expected-version', default='6ae2e515a0659525d6c861a9')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--verify-live', action='store_true')
    args = parser.parse_args()
    if args.prepare_local:
        prepare_local()
    if args.base_url:
        if args.verify_live:
            verify_live(args.base_url)
        else:
            publish(args.base_url, args.expected_version, args.apply)
