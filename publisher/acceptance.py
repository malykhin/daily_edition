#!/usr/bin/env python3
"""Inspect real Job evidence before optionally enabling the schedule. Run with kubectl configured."""
import argparse
import json
import subprocess
from pathlib import Path


def kube(*args):
    return subprocess.run(['kubectl', '-n', 'daily-edition', *args], check=True,
                          capture_output=True, text=True).stdout

def records(job, container):
    lines = kube('logs', f'job/{job}', '-c', container).splitlines()
    result = []
    for line in lines:
        try:
            result.append(json.loads(line))
        except ValueError:
            pass
    return result

def condition(job, expected):
    status = json.loads(kube('get', 'job', job, '-o', 'json'))
    if not any(c['type'] == expected and c['status'] == 'True' for c in status.get('status', {}).get('conditions', [])):
        raise RuntimeError(f'{job}: expected {expected} Job condition')
    return status['metadata']['uid']

def main():
    parser = argparse.ArgumentParser()
    for name in ('first', 'second', 'invalid', 'push-error'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--enable', action='store_true')
    args = parser.parse_args()
    if args.first == args.second:
        raise RuntimeError('first and second must be distinct manual Jobs')
    uids = {}
    sha = None
    for job, expected in ((args.first, 'published'), (args.second, 'already_published')):
        uids[job] = condition(job, 'Complete')
        logs = records(job, 'publish')
        overall = [r for r in logs if r.get('stage') == 'overall' and r.get('status') == 'success' and r.get('exit_code') == 0]
        if len(overall) != 1 or overall[0].get('result') != expected:
            raise RuntimeError(f'{job}: expected successful result={expected}')
        if sha is None:
            sha = overall[0]['commit_sha']
            if not any(r.get('stage') == 'verification' and r.get('status') == 'pages_workflow_completed' and r.get('conclusion') == 'success' for r in logs):
                raise RuntimeError('first Job did not verify GitHub Pages deployment')
            if not any(r.get('stage') == 'validation' and r.get('status') == 'success' for r in logs):
                raise RuntimeError('first Job did not validate a generated edition')
            research = records(job, 'research')
            if not any(r.get('stage') == 'research' and r.get('status') == 'success' for r in research):
                raise RuntimeError('first Job did not complete live research')
        elif overall[0]['commit_sha'] != sha:
            raise RuntimeError('main changed between runs; inspect and repeat idempotency test')
    for job, fault in ((args.invalid, 'invalid-json'), (args.push_error, 'push-error')):
        uids[job] = condition(job, 'Failed')
        if not any(r.get('stage') == 'negative_test' and r.get('fault') == fault and r.get('expected_failure') is True and r.get('remote_unchanged') is True and r.get('exit_code') == 1 for r in records(job, 'test')):
            raise RuntimeError(f'{job}: missing proof of expected failure without remote damage')
    receipt = dict(status='passed', commit_sha=sha, jobs=uids, enabled=args.enable)
    Path('acceptance-result.json').write_text(json.dumps(receipt, indent=2) + '\n')
    if args.enable:
        kube('patch', 'cronjob', 'daily-edition', '--type=merge', '-p', '{"spec":{"suspend":false}}')
    print(json.dumps(receipt, indent=2))

if __name__ == '__main__':
    main()
