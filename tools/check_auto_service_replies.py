"""Focused live-model check of staged service and product intake."""
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

SERVICE = {
    'fields': {'所属行业': '汽车服务', '完整门店名称': '测试汽车服务门店',
               '服务区域': '泰安市', '资料或服务': '预约时间'},
    'projects': {'main': [{'name': '美容护理', 'question': '您想做哪类美容护理？'}],
                 'available': [{'name': '日常保养', 'question': '您需要做哪些保养项目？'}],
                 'excluded': []},
}
PRODUCT = {
    'fields': {'所属行业': '汽车用品', '完整门店名称': '测试汽车用品门店',
               '服务区域': '泰安市', '资料或服务': '折扣'},
    'projects': {'main': [{'name': '车载脚垫', 'question': '您需要哪种车型的脚垫？'}],
                 'available': [{'name': '车载香水', 'question': '您喜欢哪种香型？'}],
                 'excluded': []},
}


def ask(chat_id, profile, message, last_asked=False):
    names = [item['name'] for group in ('main', 'available')
             for item in profile['projects'][group]]
    payload = {'appId': '6ad7276957c1257e1bb5ed75', 'chatId': chat_id,
               'stream': False, 'messages': [{'role': 'user', 'content': message}],
               'variables': {'merchant_id': 'staged-intake-check',
                             'industry': profile['fields']['所属行业'],
                             'project_selection_question': '想咨询' + '、'.join(names) + '还是其他需求？',
                             'merchant_profile': json.dumps(profile, ensure_ascii=False),
                             'last_contact_request': str(last_asked).lower(),
                             'contact_request_allowed': 'true'}}
    request = urllib.request.Request(BASE + '/api/v1/chat/completions',
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    decision = json.loads(result['choices'][0]['message']['content'])
    assert decision['action'] == 'reply', decision
    assert len(decision['reply'] + decision.get('contact_request', '')) <= 80, decision
    return decision


def question(decision):
    assert decision['fields'].get('intake_question_only') is True, decision
    assert not decision.get('contact_request'), decision
    assert '留个联系方式' not in decision['reply'], decision


def contact(decision):
    assert '留个联系方式' in decision.get('contact_request', ''), decision
    assert decision['fields'].get('intake_question_only') is not True, decision
    assert '可预约' not in decision['reply'] and '已安排' not in decision['reply'], decision


greeting = ask('greeting-' + uuid.uuid4().hex, SERVICE, '你好')
assert greeting['fields'].get('project_selection_only') is True, greeting
assert not greeting.get('contact_request'), greeting
assert all(name in greeting['reply'] for name in ('美容护理', '日常保养')), greeting

service_id = 'service-' + uuid.uuid4().hex
service_category = ask(service_id, SERVICE, '我想在你们那洗个车，精洗')
question(service_category)
assert re.search('轿车|SUV|车型', service_category['reply']), service_category
service_time = ask(service_id, SERVICE, 'SUV')
question(service_time)
assert re.search('时间|什么时候|哪天|预约', service_time['reply']), service_time
service_contact = ask(service_id, SERVICE, '周六下午2点')
contact(service_contact)
assert re.search('等|等待', service_contact['contact_request']), service_contact
assert re.search('周六|2点|两点', service_contact['reply'] + service_contact['contact_request']), service_contact
service_followup = ask(service_id, SERVICE, '那精洗大概多少钱？', True)
contact(service_followup)
assert re.search('价|费用', service_followup['reply']), service_followup

already_clear = ask('already-clear-' + uuid.uuid4().hex, SERVICE,
                    'SUV精洗，想周六下午2点到店')
contact(already_clear)
assert re.search('等|等待', already_clear['contact_request']), already_clear

product_id = 'product-' + uuid.uuid4().hex
product_category = ask(product_id, PRODUCT, '想买车载脚垫')
question(product_category)
assert re.search('车型|轿车|SUV|款式|全包围', product_category['reply']), product_category
product_quantity = ask(product_id, PRODUCT, 'SUV，想要全包围的')
question(product_quantity)
assert re.search('几套|多少|数量', product_quantity['reply']), product_quantity
product_contact = ask(product_id, PRODUCT, '两套')
contact(product_contact)
assert '价格' in product_contact['reply'] + product_contact['contact_request'], product_contact
assert re.search('折扣|优惠', product_contact['reply'] + product_contact['contact_request']), product_contact

print(json.dumps({'result': 'passed', 'greeting': greeting,
                  'service': [service_category, service_time, service_contact, service_followup],
                  'already_clear': already_clear,
                  'product': [product_category, product_quantity, product_contact]},
                 ensure_ascii=False))
