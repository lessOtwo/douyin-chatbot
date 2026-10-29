"""Focused live-model check for automotive service and unconfirmed booking slots."""
import json
import os
from pathlib import Path
import re
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get('FASTGPT_URL', 'http://127.0.0.1:3000/fastgpt').rstrip('/')
settings = dict(line.split('=', 1) for line in (ROOT / '.env').read_text(
    encoding='utf-8').splitlines() if line and not line.startswith('#') and '=' in line)
key = os.environ.get('FASTGPT_APP_KEY') or settings.get('FASTGPT_APP_KEY', '')
if not key:
    raise RuntimeError('FASTGPT_APP_KEY is not configured')

profile = {
    'fields': {'所属行业': '汽车服务', '完整门店名称': '测试汽车服务门店',
               '服务区域': '泰安市', '资料或服务': '汽车日常保养与预约咨询'},
    'projects': {'main': [{'name': '日常保养', 'question': '您需要做哪些保养项目？'}],
                 'available': [{'name': '美容护理', 'question': '您想做哪类美容护理？'}],
                 'excluded': [{'name': '专项维修', 'question': '您需要维修什么？'}]},
}
chat_id = 'auto-service-check-' + uuid.uuid4().hex


def ask(message, last_asked):
    payload = {'appId': '6ad7276957c1257e1bb5ed75', 'chatId': chat_id,
               'stream': False, 'messages': [{'role': 'user', 'content': message}],
               'variables': {'merchant_id': 'auto-service-check', 'industry': '汽车服务',
                             'project_selection_question': '想咨询日常保养还是美容护理？',
                             'merchant_profile': json.dumps(profile, ensure_ascii=False),
                             'last_contact_request': str(last_asked).lower(),
                             'contact_request_allowed': 'true'}}
    request = urllib.request.Request(BASE + '/api/v1/chat/completions',
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    return json.loads(result['choices'][0]['message']['content'])


first = ask('日常保养，想预约这周六下午2点去做', False)
second = ask('小保养，就换个空调滤芯、加防冻液这些基础的', True)
for decision in (first, second):
    assert decision['action'] == 'reply', decision
    assert len(decision['reply'] + decision.get('contact_request', '')) <= 80, decision
    assert '可预约' not in decision['reply'] and '已安排' not in decision['reply'], decision
assert re.search('周六|2点|两点', first['reply']), first
assert re.search('申请|协调|确认', first['reply']), first
assert '空调滤芯' in second['reply'] and '防冻液' in second['reply'], second
assert '可做' not in second['reply'] and '能做' not in second['reply'], second
assert not re.match(r'^(?:基础)?(?:小保养|日常保养).{0,7}(?:可做|能做)',
                    second['reply']), second
print(json.dumps({'result': 'passed', 'first': first, 'second': second}, ensure_ascii=False))
