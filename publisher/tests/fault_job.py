#!/usr/bin/env python3
"""Kubernetes-visible negative tests. All Git operations target a disposable local bare repo."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_publisher import GitPipeline, p

mode = sys.argv[1]
if mode not in ('invalid-json', 'push-error'):
    raise SystemExit('expected invalid-json or push-error')
case = GitPipeline()
case.setUp()
try:
    before = case.head()
    if mode == 'invalid-json':
        (case.work / 'generated.json').write_text('{ deliberately invalid JSON')
    else:
        hook = case.remote / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\necho "Injected push rejection" >&2\nexit 1\n')
        hook.chmod(0o700)
    with patch.object(sys, 'argv', ['publisher.py', 'publish', '--work', str(case.work)]):
        code = p.main()
    undamaged = case.head() == before
    print(json.dumps(dict(stage='negative_test', fault=mode, exit_code=code,
                          remote_unchanged=undamaged, expected_failure=code == 1 and undamaged)), flush=True)
    if code != 1 or not undamaged:
        raise SystemExit(2)
    raise SystemExit(1) # Deliberately Failed, not Complete: exercise actual Job failure visibility.
finally:
    case.doCleanups()
