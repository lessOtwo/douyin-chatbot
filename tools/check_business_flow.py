"""Targeted local message, pause and FastGPT adapter check; no real sends."""
from pathlib import Path
import json, sqlite3, subprocess, tempfile, time, urllib.error, urllib.request, uuid

root = Path(__file__).resolve().parents[1]
env = dict(line.split('=', 1) for line in (root/'.env').read_text(encoding='utf-8').splitlines() if line and not line.startswith('#') and '=' in line)
name = 'douyin-chatbot-local-flow-check'
fixture = 'douyin-chatbot-fastgpt-fixture'
base = 'http://127.0.0.1:18188'
account = 'flow-check-' + uuid.uuid4().hex[:8]
checks = []
created = []
db = None
temporary = tempfile.TemporaryDirectory(prefix='business-flow-', dir=root/'.logs')
assert (root/'.logs').resolve() in Path(temporary.name).resolve().parents

def docker(*args):
    return subprocess.run(['docker', *args], check=True, text=True, encoding='utf-8', capture_output=True).stdout.strip()

def request(path, data=None, key=True):
    body = json.dumps(data).encode() if data is not None else None
    headers = {'Content-Type':'application/json'}
    if key: headers['x-api-key'] = env['BUSINESS_API_KEY']
    req = urllib.request.Request(base+path, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read())

def wait(fn, label, seconds=35):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try: value = fn()
        except (OSError, urllib.error.URLError): value = None
        if value: return value
        time.sleep(.2)
    raise AssertionError('timed out: '+label)

def snapshot(): return request('/api/snapshot')
def calls(): return json.loads(docker('exec', fixture, 'python', '-c', "import urllib.request; print(urllib.request.urlopen('http://localhost:9000/calls').read().decode())"))['calls']
def question_list(status='open'): return request('/api/unanswered?status='+status)
def mark(label): checks.append(label); print('PASS '+label, flush=True)
def message(number, text, conversation='dialogue'):
    return {'event_id':'event-'+number,'message_id':'message-'+number,'conversation_id':conversation,'customer_id':'customer-'+conversation,'text':text}
def inbound(number, text, conversation='dialogue'):
    return request('/webhooks/platform/'+account, message(number,text,conversation))
def row_by_id(table, row_id): return next(row for row in snapshot()[table] if row['id']==row_id)

try:
    data_dir = Path(temporary.name)
    db = sqlite3.connect(data_dir/'business.sqlite')
    db.executescript((root/'business-service/src/schema.sql').read_text(encoding='utf-8'))
    db.execute("ALTER TABLE events ADD COLUMN direction TEXT NOT NULL DEFAULT 'customer'")
    db.execute("ALTER TABLE events ADD COLUMN chatwoot_synced INTEGER NOT NULL DEFAULT 0")
    db.execute("ALTER TABLE outbox ADD COLUMN source TEXT NOT NULL DEFAULT 'bot'")
    db.execute("ALTER TABLE outbox ADD COLUMN chatwoot_synced INTEGER NOT NULL DEFAULT 0")
    db.execute("ALTER TABLE outbox ADD COLUMN chatwoot_error TEXT")
    db.execute("INSERT INTO conversations(id,account_id,external_id,customer_id,mode) VALUES('legacy-paused',?,?,?,'human')",(account,'old-dialogue','old-customer'))
    db.execute("INSERT INTO events(id,account_id,event_id,message_id,conversation_id,text,revision,status,created_at) VALUES('legacy-agent',?,? ,?,'legacy-paused','old agent reply',0,'processed',1)",(account,'old-agent','old-agent'))
    db.execute("INSERT INTO outbox(id,event_id,conversation_id,revision,text,status,source,created_at) VALUES('legacy-outbox','legacy-agent','legacy-paused',0,'old agent reply','queued','human',1)")
    db.execute("INSERT INTO conversations(id,account_id,external_id,customer_id) VALUES('legacy-retry',?,?,?)",(account,'retry-dialogue','retry-customer'))
    db.execute("INSERT INTO events(id,account_id,event_id,message_id,conversation_id,text,revision,status,error,chatwoot_synced,created_at) VALUES('legacy-incoming',?,?,?,'legacy-retry','migration recovery',0,'uncertain','Chatwoot incoming sync requires review: unavailable',-1,?)",(account,'retry-event','retry-message',int(time.time())))
    db.commit(); db.close()

    accounts = data_dir/'accounts.json'
    accounts.write_text(json.dumps([{'id':account,'merchant_id':'fixture-merchant','industry':'装修装饰','fastgpt_app_id':'fixture-app','fastgpt_key_env':'FASTGPT_APP_KEY'}],ensure_ascii=False),encoding='utf-8')
    check_env = data_dir/'check.env'
    settings = {k:v for k,v in env.items() if not k.startswith('CHATWOOT_')}
    settings.update({'FASTGPT_URL':'http://'+fixture+':9000','FASTGPT_APP_KEY':'fixture-key','SEND_MODE':'mock','ADMIN_TOOLS_AUTO_LOGIN':'false'})
    check_env.write_text('\n'.join(k+'='+v for k,v in settings.items())+'\n',encoding='utf-8')
    docker('run','-d','--name',fixture,'--label','douyin-chatbot.check=business-flow','--network','douyin-chatbot_default','-v',str(root/'tools/fastgpt_fixture.py')+':/fixture.py:ro','python:3.12-alpine','python','/fixture.py')
    created.append(fixture)
    docker('run','-d','--name',name,'--label','douyin-chatbot.check=business-flow','--network','douyin-chatbot_default','--env-file',str(check_env),'-p','127.0.0.1:18188:8080','-v',str(data_dir)+':/data','-v',str(accounts)+':/app/config/accounts.json:ro','douyin-chatbot/business-service:0.1.0')
    created.append(name)
    wait(lambda: request('/health', key=False).get('status')=='ok','isolated backend health')
    assert 'Chatwoot' not in urllib.request.urlopen(base, timeout=10).read().decode()
    try: request('/api/snapshot', key=False); raise AssertionError('anonymous admin read succeeded')
    except urllib.error.HTTPError as error: assert error.code==401
    assert row_by_id('conversations','legacy-paused')['mode']=='paused'
    assert row_by_id('outbox','legacy-outbox')['status']=='cancelled'
    wait(lambda: row_by_id('events','legacy-incoming')['status']=='processed','old incoming sync recovery')
    wait(lambda: any(o['event_id']=='legacy-incoming' and o['status']=='mock_sent' for o in snapshot()['outbox']),'old incoming reply')
    mark('old Chatwoot mode and queued agent reply migrated; unsynced incoming runs locally')

    first=inbound('one','老房翻新 13800000000')
    wait(lambda: any(o['event_id']==first['id'] and o['status']=='mock_sent' for o in snapshot()['outbox']),'first reply')
    assert any(l['value']=='13800000000' for l in snapshot()['leads'])
    count=len(calls())
    assert request('/webhooks/platform/'+account,message('one','老房翻新 13800000000'))['duplicate']
    assert len(calls())==count
    try: request('/webhooks/platform/'+account,message('one','changed text')); raise AssertionError('duplicate conflict accepted')
    except urllib.error.HTTPError as error: assert error.code==409
    mark('message persists once, bot replies once and contact candidate retains provenance')

    conversation=first['conversation_id']
    paused=request('/api/conversations/'+conversation+'/control',{'mode':'paused'})
    assert paused['changed'] and paused['mode']=='paused'
    assert not request('/api/conversations/'+conversation+'/control',{'mode':'paused'})['changed']
    held=inbound('two','paused question')
    assert held['status']=='held' and row_by_id('events',held['id'])['status']=='held'
    assert len(calls())==count
    assert request('/api/conversations/'+conversation+'/control',{'mode':'bot'})['reset_session']
    third=inbound('three','新问题')
    wait(lambda: any(o['event_id']==third['id'] and o['status']=='mock_sent' for o in snapshot()['outbox']),'reply after resume')
    assert row_by_id('events',held['id'])['status']=='held'
    first_chat=next(c['chatId'] for c in calls() if c['text']=='老房翻新 13800000000')
    next_chat=next(c['chatId'] for c in calls() if c['text']=='新问题')
    assert first_chat != next_chat
    mark('pause holds incoming and resume starts a new FastGPT conversation epoch')

    slow=inbound('slow','slow unanswered')
    wait(lambda: any(c['text']=='slow unanswered' for c in calls()),'slow model call')
    request('/api/conversations/'+conversation+'/control',{'mode':'paused'})
    wait(lambda: row_by_id('events',slow['id'])['status']=='held','late model answer held',seconds=12)
    assert not any(o['event_id']==slow['id'] for o in snapshot()['outbox'])
    mark('model answer arriving after local pause never enters channel outbox')
    ui=subprocess.run(['node',str(root/'tools/check_local_conversations.cjs')],cwd=root,timeout=60,capture_output=True,text=True,encoding='utf-8',errors='replace')
    if ui.returncode: raise AssertionError('conversation browser check: '+ui.stderr[-1400:])
    mark('business admin displays customer and bot messages and controls pause locally')

    unanswered=inbound('unanswered','unanswered <img src=x onerror=alert(1)>','review-dialogue')
    wait(lambda: question_list()['total']==1,'unanswered row')
    q=question_list()['items'][0]
    assert q['event_id']==unanswered['id'] and q['question']=='unanswered <img src=x onerror=alert(1)>'
    assert next(c for c in snapshot()['conversations'] if c['id']==unanswered['conversation_id'])['mode']=='bot'
    wait(lambda: any(o['event_id']==unanswered['id'] and o['status']=='mock_sent' for o in snapshot()['outbox']),'unanswered acknowledgement')
    before=snapshot()['totals']['outbox']
    request('/api/unanswered/'+q['id']+'/status',{'status':'resolved'})
    assert question_list()['total']==0 and question_list('resolved')['total']==1
    request('/api/unanswered/'+q['id']+'/status',{'status':'open'})
    assert question_list()['total']==1 and snapshot()['totals']['outbox']==before
    subprocess.run(['node',str(root/'tools/check_unanswered_ui.cjs')],cwd=root,check=True,timeout=60,capture_output=True,text=True,encoding='utf-8',errors='replace')
    mark('unanswered review, status changes and browser display stay independent of sending')

    legacy=inbound('legacy','legacy-handoff','legacy-handoff-dialogue')
    wait(lambda: question_list()['total']==2,'legacy handoff review')
    wait(lambda: any(o['event_id']==legacy['id'] and o['status']=='mock_sent' for o in snapshot()['outbox']),'legacy acknowledgement')
    reply=next(o['text'] for o in snapshot()['outbox'] if o['event_id']==legacy['id'])
    assert '暂时无法提供人工回复' in reply
    assert next(c for c in snapshot()['conversations'] if c['id']==legacy['conversation_id'])['mode']=='bot'
    stop=inbound('stop','stop','stop-dialogue')
    wait(lambda: any(o['event_id']==stop['id'] and o['status']=='mock_sent' for o in snapshot()['outbox']),'stop acknowledgement')
    assert next(c for c in snapshot()['conversations'] if c['id']==stop['conversation_id'])['mode']=='paused'
    assert inbound('after-stop','继续','stop-dialogue')['status']=='held'
    mark('legacy handoff remains truthful; stop acknowledges once then pauses robot')

    record={'date':'2026-10-20','result':'passed','checks':checks,'fastgpt':'explicit local HTTP fixture','real_model_verified':False,'real_channel_verified':False,'full_tests_run':False,'production_deployed':False}
    (root/'artifacts/business-flow-checks.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False),flush=True)
except Exception:
    if name in created:
        print('Isolated backend logs: '+docker('logs','--tail','12',name),flush=True)
    raise
finally:
    if db is not None: db.close()
    for item in reversed(created):
        labels=docker('inspect','--format','{{index .Config.Labels "douyin-chatbot.check"}}',item)
        if labels=='business-flow': docker('rm','-f',item)
    temporary.cleanup()
