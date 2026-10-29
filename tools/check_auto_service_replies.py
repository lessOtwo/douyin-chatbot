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
               '服务区域': '泰安市', '话术类别': '到店/上门服务型'},
    'projects': {'main': [{'name': '美容护理', 'question': '您想做哪类美容护理？'}],
                 'available': [{'name': '日常保养', 'question': '您需要做哪些保养项目？'}],
                 'excluded': []},
}
WHOLESALE = {
    'fields': {'所属行业': '汽车用品', '完整门店名称': '测试汽车用品门店',
               '服务区域': '泰安市', '话术类别': '工厂批发型'},
    'projects': {'main': [{'name': '车载脚垫', 'question': '您需要哪种车型的脚垫？'}],
                 'available': [{'name': '车载香水', 'question': '您喜欢哪种香型？'}],
                 'excluded': []},
}
RETAIL = {
    'fields': {'所属行业': '汽车用品', '完整门店名称': '测试零售门店',
               '服务区域': '泰安市', '话术类别': '零售型'},
    'projects': WHOLESALE['projects'],
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
    assert '留个联系方式' in decision['reply'] + decision.get('contact_request', ''), decision
    assert decision['fields'].get('intake_question_only') is not True, decision
    assert '可预约' not in decision['reply'] and '已安排' not in decision['reply'], decision


greeting = ask('greeting-' + uuid.uuid4().hex, SERVICE, '你好')
assert greeting['fields'].get('project_selection_only') is True, greeting
assert not greeting.get('contact_request'), greeting
assert all(name in greeting['reply'] for name in ('美容护理', '日常保养')), greeting

service_id = 'service-' + uuid.uuid4().hex
service_time = ask(service_id, SERVICE, '我想在你们那洗个车，精洗')
question(service_time)
assert re.search('时间|什么时候|哪天|预约', service_time['reply']), service_time
assert not re.search('轿车|SUV|车型', service_time['reply']), service_time
service_contact = ask(service_id, SERVICE, '周六下午2点')
contact(service_contact)
assert re.search('预约|档期|时间|时段', service_contact['contact_request']), service_contact
assert '工作人员' in service_contact['contact_request'], service_contact
assert re.search('空闲|有人|接待|上门|可否|能否|安排', service_contact['contact_request']), service_contact
assert re.search('周六|2点|两点', service_contact['reply'] + service_contact['contact_request']), service_contact
service_followup = ask(service_id, SERVICE, '那精洗大概多少钱？', True)
contact(service_followup)
assert re.search('价|费用', service_followup['reply']), service_followup

already_clear = ask('already-clear-' + uuid.uuid4().hex, SERVICE,
                    'SUV精洗，想周六下午2点到店')
contact(already_clear)
assert re.search('周六|2点|两点|预约|档期|时间|时段', already_clear['contact_request']), already_clear
assert re.search('空闲|有人|接待|上门|可否|能否|安排', already_clear['contact_request']), already_clear

wholesale_id = 'wholesale-' + uuid.uuid4().hex
wholesale_quantity = ask(wholesale_id, WHOLESALE, '想批发车载脚垫')
question(wholesale_quantity)
assert re.search('几|多少|数量|起订', wholesale_quantity['reply']), wholesale_quantity
wholesale_contact = ask(wholesale_id, WHOLESALE, '两百套')
contact(wholesale_contact)
assert re.search('折扣|优惠', wholesale_contact['reply'] + wholesale_contact['contact_request']), wholesale_contact
assert re.search('门店|工作人员', wholesale_contact['contact_request']), wholesale_contact

retail_id = 'retail-' + uuid.uuid4().hex
retail_model = ask(retail_id, RETAIL, '想买车载脚垫')
question(retail_model)
assert re.search('车型|轿车|SUV|款式|档次|型号', retail_model['reply']), retail_model
retail_contact = ask(retail_id, RETAIL, 'SUV，全包围的')
contact(retail_contact)
assert re.search('全包围|贴合|覆盖|防护|清洁|材质|差异|特点|特性', retail_contact['reply']), retail_contact
assert re.search('型号|款式|档次|差异|特点|特性', retail_contact['contact_request']), retail_contact
assert re.search('门店|工作人员', retail_contact['contact_request']), retail_contact
assert not re.search('几套|多少套|数量', retail_contact['reply'] + retail_contact['contact_request']), retail_contact

print(json.dumps({'result': 'passed', 'greeting': greeting,
                  'service': [service_time, service_contact, service_followup],
                  'already_clear': already_clear,
                  'wholesale': [wholesale_quantity, wholesale_contact],
                  'retail': [retail_model, retail_contact]},
                 ensure_ascii=False))
