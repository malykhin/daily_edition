import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import publisher as p


def fixture():
    date = p.today()
    url = 'https://news.test/article'
    weather_url = 'https://weather.test/forecast'
    days = [dict(date=(dt.date.fromisoformat(date) + dt.timedelta(days=i)).isoformat(),
                 high=15, low=7, url=weather_url) for i in range(4)]
    edition = dict(date=date, status='published',
                   intro=' '.join(['A concise original researched briefing for Vancouver readers.'] * 75),
                   weather=dict(title='Vancouver Celsius forecast', summary=' '.join(d['date'] + ': 15/7°C' for d in days) + ' ' + weather_url, high=15, low=7),
                   stories=[dict(section='LOCAL', title='Local development', summary='A verified new local development.', url=url, tag='BC')],
                   markets=[], events=[], meetups=[], dining=[], watchers=[])
    evidence = [dict(path=path, url=link, checked_at=p.now().isoformat(), as_of=date,
                     kind=kind, event_date='', venue='', registration_url='')
                for path, link, kind in [('weather', weather_url, 'weather'), ('stories/0', url, 'news')]]
    omissions = [dict(section=s, reason='No fresh verified development was available in checked sources.')
                 for s in ['TECHNOLOGY', 'promotion', 'events', 'meetups', 'dining', 'TSX', 'S&P 500', 'Nasdaq', 'Bank of Canada', 'inflation', 'recession']]
    return dict(edition=edition, evidence=evidence, weather_days=days, omissions=omissions)

class Validation(unittest.TestCase):
    def validate(self, payload):
        return p.validate(payload, p.today(), probe_url=lambda _: None)
    def test_valid(self):
        self.validate(fixture())
    def test_invalid_variants(self):
        changes = [lambda x: x['edition'].update(date='2000-01-01'),
                   lambda x: x['edition']['weather'].update(high=True),
                   lambda x: x['edition'].update(secret='private'),
                   lambda x: x.update(evidence=[]),
                   lambda x: x['evidence'][0].update(checked_at='2000-01-01T00:00:00Z'),
                   lambda x: x['evidence'][1].update(as_of='2000-01-01'),
                   lambda x: x['edition'].update(intro='VEQT placeholder'),
                   lambda x: x['weather_days'].pop(),
                   lambda x: x.update(omissions=[])]
        for change in changes:
            with self.subTest(change=change):
                data = fixture()
                change(data)
                with self.assertRaises(ValueError):
                    self.validate(data)
    def test_repeated_story(self):
        data = fixture()
        with self.assertRaisesRegex(ValueError, 'repeated'):
            p.validate(data, p.today(), probe_url=lambda _: None, previous=[data['edition']])
    def test_url_rejection(self):
        for url in ['file:///etc/passwd', 'https://example.com/x', 'http://news.test', 'https://user:pass@news.test']:
            with self.assertRaises(ValueError):
                p.public_url(url)
        with patch.object(p.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaisesRegex(ValueError, 'nonpublic'):
                p.public_url('https://news.test')
    def test_duplicate_key(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / 'a.json'
            path.write_text('{"date":"one","date":"two"}')
            with self.assertRaises(ValueError):
                p.load(path)
    def test_event_date_registration(self):
        data = fixture()
        data['edition']['events'] = [dict(date=p.today() + ' Venue', title='Concert', description='New concert', url='https://events.test/register')]
        data['evidence'].append(dict(path='events/0', url='https://events.test/info', checked_at=p.now().isoformat(), as_of=p.today(), kind='event', event_date='2000-01-01', venue='Venue', registration_url='https://events.test/register'))
        with self.assertRaisesRegex(ValueError, 'past'):
            self.validate(data)

class GitPipeline(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / 'remote.git'
        self.seed = self.root / 'seed'
        self.cmd('git', 'init', '--bare', '--initial-branch=main', str(self.remote))
        self.cmd('git', 'clone', str(self.remote), str(self.seed))
        self.cmd('git', '-C', str(self.seed), 'config', 'user.name', 'Test')
        self.cmd('git', '-C', str(self.seed), 'config', 'user.email', 'test@example.test')
        (self.seed / 'content/editions').mkdir(parents=True)
        (self.seed / 'src/lib').mkdir(parents=True)
        (self.seed / 'src/lib/editions.ts').write_text('reference schema')
        (self.seed / 'unchanged.txt').write_text('preserve this')
        self.cmd('git', '-C', str(self.seed), 'add', '.')
        self.cmd('git', '-C', str(self.seed), 'commit', '-m', 'Initial')
        self.cmd('git', '-C', str(self.seed), 'push', 'origin', 'main')
        self.patch = patch.object(p, 'REPO', str(self.remote))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.envpatch = patch.object(p, 'credential_env', return_value=os.environ.copy())
        self.envpatch.start()
        self.addCleanup(self.envpatch.stop)
        self.work = self.root / 'work'
        self.work.mkdir()
        p.write(self.work / 'state.json', dict(execution_id='test', start=p.now().isoformat(), date=p.today(), status='running', skip=False))
        self.log = p.Log(self.work)
        p.write(self.work / 'generated.json', fixture())
        self.real_validate = p.validate
        def checked(data, date, **kwargs):
            return self.real_validate(data, date, probe_url=lambda _: None, **kwargs)
        self.vpatch = patch.object(p, 'validate', side_effect=checked)
        self.vpatch.start()
        self.addCleanup(self.vpatch.stop)
    def cmd(self, *args):
        return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()
    def head(self):
        return self.cmd('git', '--git-dir', str(self.remote), 'rev-parse', 'main')
    def publish(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return p.publish(self.work, self.log)
    def test_publish_readback_duplicate(self):
        before = self.head()
        self.assertEqual(self.publish(), 'published')
        after = self.head()
        self.assertNotEqual(before, after)
        files = self.cmd('git', '--git-dir', str(self.remote), 'diff-tree', '--no-commit-id', '--name-only', '-r', after)
        self.assertEqual(files, f'content/editions/{p.today()}.json')
        second = self.root / 'second'
        second.mkdir()
        p.write(second / 'state.json', self.log.state)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(p.publish(second, p.Log(second)), 'already_published')
        self.assertEqual(self.head(), after)
    def test_invalid_json_no_commit(self):
        before = self.head()
        (self.work / 'generated.json').write_text('{ invalid json')
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.head(), before)
    def test_push_rejection_no_remote_damage(self):
        before = self.head()
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o700)
        with self.assertRaisesRegex(RuntimeError, 'push rejected'):
            self.publish()
        self.assertEqual(self.head(), before)
    def test_concurrent_update_safe_retry(self):
        original_git = p.git
        did_race = False
        def racing(repo, *args, **kwargs):
            nonlocal did_race
            if args[:1] == ('push',) and not did_race:
                did_race = True
                (self.seed / 'other.txt').write_text('concurrent unrelated update')
                self.cmd('git', '-C', str(self.seed), 'add', '.')
                self.cmd('git', '-C', str(self.seed), 'commit', '-m', 'Concurrent')
                self.cmd('git', '-C', str(self.seed), 'push', 'origin', 'main')
            return original_git(repo, *args, **kwargs)
        with patch.object(p, 'git', side_effect=racing):
            self.assertEqual(self.publish(), 'published')
        self.assertEqual(self.cmd('git', '--git-dir', str(self.remote), 'show', 'main:other.txt'), 'concurrent unrelated update')
    def test_concurrent_published_edition_wins(self):
        original_git = p.git
        winner = fixture()['edition']
        winner['intro'] += ' A concurrent publisher won.'
        did_race = False
        def racing(repo, *args, **kwargs):
            nonlocal did_race
            if args[:1] == ('push',) and not did_race:
                did_race = True
                p.write(self.seed / f'content/editions/{p.today()}.json', winner)
                self.cmd('git', '-C', str(self.seed), 'add', '.')
                self.cmd('git', '-C', str(self.seed), 'commit', '-m', 'Winner')
                self.cmd('git', '-C', str(self.seed), 'push', 'origin', 'main')
            return original_git(repo, *args, **kwargs)
        with patch.object(p, 'git', side_effect=racing):
            self.assertEqual(self.publish(), 'already_published')
        actual = json.loads(self.cmd('git', '--git-dir', str(self.remote), 'show', f'main:content/editions/{p.today()}.json'))
        self.assertEqual(actual, winner)
    def test_prepare_existing_skips_research(self):
        p.write(self.seed / f'content/editions/{p.today()}.json', fixture()['edition'])
        self.cmd('git', '-C', str(self.seed), 'add', '.')
        self.cmd('git', '-C', str(self.seed), 'commit', '-m', 'Existing')
        self.cmd('git', '-C', str(self.seed), 'push', 'origin', 'main')
        (self.work / 'generated.json').unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            p.prepare(self.work, self.log)
            p.research(self.work, self.log)
        self.assertTrue(p.load(self.work / 'state.json')['skip'])
        self.assertFalse((self.work / 'generated.json').exists())
    def test_main_failure_exit_and_logs(self):
        (self.work / 'generated.json').write_text('{ invalid json')
        with patch.object(sys, 'argv', ['publisher.py', 'publish', '--work', str(self.work)]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(p.main(), 1)
        state = p.load(self.work / 'state.json')
        self.assertEqual(state['status'], 'failure')
        self.assertEqual(state['failed_stage'], 'validation')
        self.assertIn('failure', (self.work / 'logs/validation.jsonl').read_text())

class Research(unittest.TestCase):
    def test_isolated_cli_success_and_redacted_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            auth = root / 'auth'
            auth.mkdir()
            token = 'dummy-private-refresh-token-12345'
            p.write(auth / 'auth.json', dict(auth_mode='chatgpt', tokens={'refresh_token': token}))
            work = root / 'work'
            work.mkdir()
            p.write(work / 'state.json', dict(execution_id='research-test', date=p.today(), skip=False))
            p.write(work / 'schema.json', p.SCHEMA)
            (work / 'prompt.txt').write_text('Test prompt; never research real news in this test.')
            cli = root / 'codex'
            cli.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
assert 'GITHUB_TOKEN' not in os.environ
assert 'OPENAI_API_KEY' not in os.environ
assert 'features.shell_tool=false' in sys.argv
assert 'web_search="live"' in sys.argv
sys.stdin.read()
pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text('{}')
print(json.dumps({'type':'turn.completed'}))
''')
            cli.chmod(0o700)
            environment = dict(CODEX_HOME=str(auth), PATH=str(root) + ':' + os.environ['PATH'],
                               GITHUB_TOKEN='must-not-reach-agent', OPENAI_API_KEY='must-not-reach-agent')
            with patch.dict(os.environ, environment), contextlib.redirect_stdout(io.StringIO()):
                p.research(work, p.Log(work))
            self.assertTrue((work / 'generated.json').exists())
            cli.write_text('''#!/usr/bin/env python3
import json, sys
sys.stdin.read()
print(json.dumps({'type':'turn.failed','error':{'message':'dummy-private-refresh-token-12345 auth failure'}}))
print('dummy-private-refresh-token-12345', file=sys.stderr)
sys.exit(1)
''')
            with patch.dict(os.environ, environment), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'Codex failed'):
                    p.research(work, p.Log(work))
            logs = (work / 'logs/research.jsonl').read_text()
            self.assertNotIn(token, logs)
            self.assertIn('[REDACTED]', logs)

if __name__ == '__main__':
    unittest.main()
