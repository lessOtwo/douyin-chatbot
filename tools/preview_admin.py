"""Run only the controlled local UI preview; never sends real channel messages."""
import os
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
values = dict(line.split("=",1) for line in (root/".env").read_text(encoding="utf-8").splitlines() if line and not line.startswith("#") and "=" in line)
runtime_env = {**os.environ,**values,"BUSINESS_URL":"http://127.0.0.1:18089","MOCK_PORT":"19001",
               "BIND":"127.0.0.1:18089","DATABASE_PATH":str(root/".logs/preview.sqlite"),
               "ACCOUNTS_FILE":str(root/"config/accounts.mock.json"),"TYPEBOT_URL":"http://127.0.0.1:19001",
               "FASTGPT_URL":"http://127.0.0.1:19001","FASTGPT_APP_KEY":"mock-key","SEND_MODE":"mock","SECURE_COOKIE":"false"}
(root/".logs").mkdir(exist_ok=True)
children = [subprocess.Popen([os.sys.executable,str(root/"business-service/tests/mock_engines.py")],env=runtime_env),
            subprocess.Popen([str(root/"business-service/target/debug/douyin-chatbot-service.exe")],env=runtime_env)]
try:
    children[1].wait()
finally:
    for child in children:
        child.terminate()
