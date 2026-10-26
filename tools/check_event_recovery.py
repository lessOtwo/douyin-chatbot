"""Focused workflow-error/retry checks in a disposable Docker instance; no live data."""
import argparse
from collections import defaultdict
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid


def run(image, browser_check=None):
    root = Path(__file__).resolve().parents[1]
    key = 'isolated-recovery-check-' + uuid.uuid4().hex
    calls, release = defaultdict(list), threading.Event()

    class Engine(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            text = request['messages'][0]['content']
            calls[text].append(request['chatId'])
            if text == 'retry-check' and len(calls[text]) == 2:
                release.wait(15)
            if text == 'long-error':
                content = '错误' * 6000
            elif len(calls[text]) == 1:
                content = 'not-json <img src=x onerror="window.errorXss=true">'
            else:
                content = json.dumps({'reply': '重试成功', 'action': 'reply', 'fields': {}}, ensure_ascii=False)
            body = json.dumps({'choices': [{'message': {'content': content}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    engine = ThreadingHTTPServer(('0.0.0.0', 0), Engine)
    threading.Thread(target=engine.serve_forever, daemon=True).start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    name = 'douyin-event-check-' + uuid.uuid4().hex[:10]
    checks = []
    with tempfile.TemporaryDirectory(prefix='douyin-event-check-') as folder:
        fixture = Path(folder)
        (fixture / 'accounts.json').write_bytes((root / 'config/accounts.mock.json').read_bytes())
        command = ['docker', 'run', '-d', '--name', name,
                   '-p', f'127.0.0.1:{port}:8080', '--mount', f'type=bind,source={fixture},target=/fixtures']
        for name_, value in {
            'DATABASE_PATH': '/fixtures/test.sqlite', 'ACCOUNTS_FILE': '/fixtures/accounts.json',
            'BUSINESS_API_KEY': key, 'INTERNAL_API_KEY': key,
            'FASTGPT_URL': f'http://host.docker.internal:{engine.server_port}',
            'FASTGPT_APP_KEY': 'fixture-key', 'SEND_MODE': 'mock',
        }.items():
            command += ['-e', f'{name_}={value}']
        try:
            subprocess.run(command + [image], check=True, capture_output=True)

            def call(path, payload=None, auth=True):
                request = urllib.request.Request(base + path,
                    data=None if payload is None else json.dumps(payload).encode(),
                    headers={'Content-Type': 'application/json', **({'x-api-key': key} if auth else {})})
                try:
                    response = urllib.request.urlopen(request, timeout=10)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    return response.status, json.load(response)

            def wait(predicate):
                for _ in range(150):
                    snapshot = call('/api/snapshot')[1]
                    if predicate(snapshot):
                        return snapshot
                    time.sleep(.1)
                raise AssertionError('Isolated check timed out')

            for _ in range(100):
                try:
                    if call('/health')[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(.1)
            else:
                raise AssertionError('Fixture did not start')

            def incoming(text):
                identity = uuid.uuid4().hex
                status, event = call('/webhooks/platform/renovation-demo', {
                    'event_id': identity, 'message_id': identity, 'conversation_id': identity,
                    'customer_id': 'fixture', 'text': text})
                assert status == 200
                wait(lambda s: any(e['id'] == event['id'] and e['status'] == 'uncertain' for e in s['events']))
                return event

            event = incoming('retry-check')
            retry = '/api/events/' + event['id'] + '/retry'
            saved = next(e for e in call('/api/snapshot')[1]['events'] if e['id'] == event['id'])
            assert 'not-json <img' in saved['error']
            assert call(retry, {}, auth=False)[0] == 401
            assert call('/api/events/missing/retry', {})[0] == 404
            assert call(retry, {})[0] == 200
            assert call(retry, {})[0] == 409
            wait(lambda s: any(e['id'] == event['id'] and e['status'] == 'processing' for e in s['events']))
            release.set()
            result = wait(lambda s: any(o['event_id'] == event['id'] and o['status'] == 'mock_sent' for o in s['outbox']))
            assert len(calls['retry-check']) == 2 and calls['retry-check'][0] != calls['retry-check'][1]
            assert len([o for o in result['outbox'] if o['event_id'] == event['id']]) == 1
            assert next(c for c in result['conversations'] if c['id'] == event['conversation_id'])['reply_rounds'] == 1
            assert next(e for e in result['events'] if e['id'] == event['id'])['error'] is None
            assert call(retry, {})[0] == 409
            checks += ['saved model response', 'authentication', 'missing event', 'retry success',
                       'duplicate retry rejected', 'fresh workflow chat', 'one reply and one round']

            long = incoming('long-error')
            error = next(e for e in call('/api/snapshot')[1]['events'] if e['id'] == long['id'])['error']
            assert len(error) < 8200 and error.endswith('[详情已截断]')
            checks.append('bounded error details')

            # Seed only while the fixture is stopped: Windows and Linux SQLite locks differ.
            subprocess.run(['docker', 'stop', name], check=True, capture_output=True)
            with closing(sqlite3.connect(fixture / 'test.sqlite')) as db:
                db.create_function('unixepoch', 0, lambda: int(time.time()))
                def seed(label, *, mode='bot', rounds=0, revision=1, status='uncertain', direction='customer', account='renovation-demo'):
                    db.execute('INSERT INTO conversations(id,account_id,external_id,customer_id,revision,mode,reply_rounds) VALUES(?,?,?,?,?,?,?)',
                               (label, account, label, 'fixture', revision, mode, rounds))
                    db.execute('INSERT INTO events(id,account_id,event_id,message_id,conversation_id,text,revision,status,direction) VALUES(?,?,?,?,?,?,?,?,?)',
                               (label, account, label, label, label, label, 1, status, direction))
                    return label
                rejected = [seed('paused', mode='paused'), seed('cap', rounds=50), seed('stale', revision=2),
                            seed('agent', direction='agent'), seed('processed', status='processed'),
                            seed('removed', account='second-account')]
                db.execute("INSERT INTO removed_accounts(id) VALUES('second-account')")
                for state in ['queued', 'sending', 'accepted', 'mock_sent', 'uncertain', 'blocked', 'cancelled']:
                    label = seed('send-' + state)
                    db.execute('INSERT INTO outbox(id,event_id,conversation_id,revision,text,status) VALUES(?,?,?,?,?,?)',
                               (label, label, label, 1, 'fixture', state))
                    rejected.append(label)
                label = seed('other-blocker')
                db.execute('INSERT INTO events(id,account_id,event_id,message_id,conversation_id,text,revision,status) VALUES(?,?,?,?,?,?,?,?)',
                           ('sibling','renovation-demo','sibling','sibling',label,'fixture',1,'blocked'))
                rejected.append(label)
                db.commit()
            subprocess.run(['docker', 'start', name], check=True, capture_output=True)
            for _ in range(100):
                try:
                    if call('/health')[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(.1)
            for label in rejected:
                status, response = call('/api/events/' + label + '/retry', {})
                assert status == 409, (label, status, response)
            checks += ['paused and capped conversations', 'stale revision', 'agent/processed events',
                       'removed account', 'all existing send states', 'other workflow blocker']

            if browser_check:
                browser_event = incoming('browser-check')
                context = fixture / 'browser.json'
                context.write_text(json.dumps({'base': base, 'key': key, 'event': browser_event['id']}), encoding='utf-8')
                subprocess.run(['node', browser_check, str(context)], check=True)
                wait(lambda s: any(o['event_id'] == browser_event['id'] and o['status'] == 'mock_sent' for o in s['outbox']))
                checks.append('browser details and retry')
            output = {'result': 'passed', 'checks': checks, 'live_data_modified': False, 'full_suite_run': False}
            (root / 'artifacts/event-recovery-checks.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print(json.dumps(output, ensure_ascii=False))
        finally:
            release.set()
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
            engine.shutdown()
            engine.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='douyin-chatbot/business-service:0.1.0')
    parser.add_argument('--browser-check')
    args = parser.parse_args()
    run(args.image, args.browser_check)
