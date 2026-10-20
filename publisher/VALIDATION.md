# Validation performed on 2026-10-20

- `python3 -m unittest discover -s publisher/tests -v`: 17 tests passed.
  Includes schema/freshness/URL checks, isolated Codex command invocation with a fake
  executable, secret redaction, real local Git commits and read-back, duplicate runs,
  unrelated branch races, concurrent published-edition preservation, invalid JSON,
  actual pre-receive push rejection, application exit codes/logs, and acceptance gating.
- `python3 publisher/tests/fault_job.py invalid-json`: exited 1, validation failure,
  `expected_failure:true`, `remote_unchanged:true`.
- `python3 publisher/tests/fault_job.py push-error`: exited 1, publishing failure,
  `expected_failure:true`, `remote_unchanged:true`.
- All seven Kubernetes YAML documents parsed successfully using js-yaml. No cluster
  server-side validation has been performed.
- `ASTRO_TELEMETRY_DISABLED=1 npm run build`: succeeded; six static pages generated.
  The repository has no npm lockfile; existing dependencies were installed without
  creating one. No website source, edition or existing workflow was changed.
- Real GitHub initialization detected the existing published `2026-10-20` edition
  at main SHA `8433ac4baf76a270de9d911d6ca13915f2144ba3`, returned success, and
  did not modify it. The initial restricted-network attempt failed visibly; the
  authorized network retry succeeded.

Not completed: real Codex news generation, a new edition pushed to GitHub, deployment
of that new edition, actual Kubernetes Job executions and full live acceptance. This
workspace has no Codex CLI, kubectl, Docker, cluster context, existing runtime image
reference or mounted publisher credentials. The existing published date cannot be
reused to force a full publication test. The CronJob remains suspended until the real
manual acceptance gate passes. Public product descriptions are also needed to make
promotion opportunities verifiably relevant.
