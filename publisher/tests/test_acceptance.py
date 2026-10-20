import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import acceptance as a

class AcceptanceGate(unittest.TestCase):
    def logs(self, job, container):
        if container == 'research':
            return [dict(stage='research', status='success')]
        if container == 'test':
            return [dict(stage='negative_test', fault='invalid-json' if job == 'invalid' else 'push-error', expected_failure=True, remote_unchanged=True, exit_code=1)]
        return [dict(stage='overall', status='success', exit_code=0,
                     result='published' if job == 'first' else 'already_published', commit_sha='sha'),
                dict(stage='validation', status='success'),
                dict(stage='verification', status='pages_workflow_completed', conclusion='success')]
    def argv(self):
        return ['acceptance.py', '--first', 'first', '--second', 'second', '--invalid', 'invalid', '--push-error', 'push', '--enable']
    def test_enables_only_after_full_evidence(self):
        with patch.object(sys, 'argv', self.argv()), patch.object(a, 'condition', side_effect=lambda j, c: j+'-uid'), patch.object(a, 'records', side_effect=self.logs), patch.object(a.Path, 'write_text'), patch.object(a, 'kube') as kube, contextlib.redirect_stdout(io.StringIO()):
            a.main()
        self.assertEqual(kube.call_count, 1)
        self.assertIn('{"spec":{"suspend":false}}', kube.call_args.args)
    def test_incomplete_evidence_cannot_enable(self):
        def incomplete(job, container):
            records = self.logs(job, container)
            return [r for r in records if r.get('status') != 'pages_workflow_completed']
        with patch.object(sys, 'argv', self.argv()), patch.object(a, 'condition', return_value='uid'), patch.object(a, 'records', side_effect=incomplete), patch.object(a, 'kube') as kube:
            with self.assertRaisesRegex(RuntimeError, 'Pages'):
                a.main()
        kube.assert_not_called()
    def test_negative_job_must_be_failed(self):
        with patch.object(a, 'kube', return_value='{"metadata":{"uid":"x"},"status":{"conditions":[{"type":"Complete","status":"True"}]}}'):
            with self.assertRaisesRegex(RuntimeError, 'Failed'):
                a.condition('invalid', 'Failed')
