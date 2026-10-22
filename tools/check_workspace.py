"""Configuration/source checks only; does not run any project's full test suite."""
import json
import os
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]
checks = []


def command(name, args, extra_env=None):
    result = subprocess.run(args,cwd=root,env={**os.environ,**(extra_env or {})},text=True,encoding="utf-8",capture_output=True)
    checks.append({"name":name,"passed":result.returncode==0})
    if result.returncode:
        raise RuntimeError(name+": "+result.stderr[-2000:])


own_files = [p for p in root.rglob('*') if p.is_file() and not any(part in {'.git','Typebot','Chatwoot','FastGPT','target','.logs','__pycache__'} for part in p.relative_to(root).parts)]
for file in own_files:
    if file.suffix in {'.md','.json','.yaml','.toml','.ps1','.sh','.py','.rs','.html','.cjs','.sql'}:
        text = file.read_text(encoding='utf-8')
        assert not text.startswith('\ufeff'), str(file)+' has BOM'
    if file.suffix=='.json':
        json.loads(file.read_text(encoding='utf-8'))
checks.append({"name":"own text files UTF-8 without BOM and JSON syntax","passed":True})
log_dir = root/'.logs'
log_dir.mkdir(exist_ok=True)
html = (root/'business-service/web/index.html').read_text(encoding='utf-8')
(log_dir/'admin.js').write_text(re.search(r'<script>(.*?)</script>',html,re.S).group(1),encoding='utf-8')
command('embedded admin JavaScript syntax',['node','--check',str(log_dir/'admin.js')])
command('local Compose configuration',['docker','compose','-f','compose.yaml','config','--quiet'])
command('production Compose render with example domains only',['docker','compose','-f','compose.yaml','-f','compose.production.yaml','config','--quiet'],{
    'BUSINESS_HOST':'chat.example.test',
    'FASTGPT_HOST':'knowledge.example.test','SMTP_USERNAME':'example-user','SMTP_PASSWORD':'example-only'
})
command('source build override configuration',['docker','compose','-f','compose.yaml','-f','compose.source.yaml','config','--quiet'])
command('business source formatting',['cargo','fmt','--manifest-path','business-service/Cargo.toml','--check'])
record={"checks":checks,"full_tests_run":False,"production_deployed":False,"real_channel_verified":False}
(root/'artifacts/config-checks.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(record,ensure_ascii=False))
