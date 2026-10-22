"""Read-only audit of the completed local Chatwoot retirement."""
from pathlib import Path
import json, subprocess, urllib.error, urllib.request

root = Path(__file__).resolve().parents[1]
env = dict(line.split('=',1) for line in (root/'.env').read_text(encoding='utf-8').splitlines() if line and not line.startswith('#') and '=' in line)
def docker(*args):
    return subprocess.run(['docker',*args],check=True,capture_output=True,text=True,encoding='utf-8').stdout.strip().splitlines()

project = docker('ps','-a','--filter','label=com.docker.compose.project=douyin-chatbot','--format','{{.Names}}')
assert not any('chatwoot' in name or 'mailpit' in name for name in project), project
assert not docker('ps','-a','--filter','label=douyin-chatbot.check=business-flow','--format','{{.Names}}')
services = docker('compose','-f',str(root/'compose.yaml'),'config','--services')
assert not any('chatwoot' in name or 'mailpit' in name for name in services), services
assert not (root/'Chatwoot').exists()
assert 'Chatwoot' not in (root/'.gitmodules').read_text(encoding='utf-8')
assert not (root/'.git/modules/Chatwoot').exists()
images = docker('image','ls','--format','{{.Repository}}:{{.Tag}}')
assert not any(name.startswith(('chatwoot/','douyin-chatbot/chatwoot:','axllent/mailpit:')) or name=='pgvector/pgvector:0.8.2-pg16' for name in images), images
assert not any(key.startswith(('CHATWOOT_','SMTP_')) for key in env)
redis = subprocess.run(['docker','exec','-e','REDISCLI_AUTH='+env['REDIS_PASSWORD'],'douyin-chatbot-redis-1','redis-cli','-n','3','DBSIZE'],check=True,capture_output=True,text=True,encoding='utf-8').stdout.strip()
assert redis=='0',redis
volumes = docker('volume','ls','--format','{{.Name}}')
retained = ['douyin-chatbot_chatwoot-postgres-data','douyin-chatbot_chatwoot-storage']
assert all(name in volumes for name in retained)
with urllib.request.urlopen('http://127.0.0.1:8088/health',timeout=15) as response:
    assert json.load(response)['status']=='ok'
for path, headers in [('/webhooks/chatwoot',{}),('/admin-tools/chatwoot',{'x-api-key':env['BUSINESS_API_KEY']})]:
    try:
        urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8088'+path,data=b'{}',headers=headers),timeout=15)
        raise AssertionError('retired endpoint accepted request: '+path)
    except urllib.error.HTTPError as error:
        assert error.code==404,(path,error.code)
record={'date':'2026-10-20','result':'passed','local_runtime_chatwoot_removed':True,'source_and_git_module_removed':True,'dedicated_images_removed':True,'redis_db3_keys_remaining':0,'retained_historical_volumes':retained,'business_healthy':True,'full_tests_run':False,'production_deployed':False}
(root/'artifacts/chatwoot-removal.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(record,ensure_ascii=False))
