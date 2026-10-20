#!/usr/bin/env python3
"""Trusted deterministic stages; never execute scripts from the fetched newspaper repo."""
import argparse
import datetime as dt
import hashlib
import fcntl
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo
from schema import SCHEMA, EDITION, check

UTC = dt.timezone.utc
ROOT = Path(__file__).resolve().parent
REPO = "https://github.com/malykhin/daily_edition.git"
API = "https://api.github.com/repos/malykhin/daily_edition"

def now():
    return dt.datetime.now(UTC)
def today():
    return now().astimezone(ZoneInfo("America/Vancouver")).date().isoformat()
def load(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=unique,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)

class Log:
    def __init__(self, work):
        self.work = work
        self.state = load(work / "state.json")
        (work / "logs").mkdir(exist_ok=True)
        self.stage = "initialize"
    def emit(self, stage, status, **fields):
        record = dict(execution_id=self.state["execution_id"], timestamp=now().isoformat(),
                      stage=stage, status=status, **fields)
        line = json.dumps(record, ensure_ascii=False)
        print(line, flush=True)
        with (self.work / "logs" / f"{stage}.jsonl").open("a") as f:
            f.write(line + "\n")
    def finish(self, status, code, **fields):
        self.state.update(status=status, exit_code=code, end=now().isoformat(), **fields)
        write(self.work / "state.json", self.state)
        self.emit("overall", status, exit_code=code, **fields)
        termination = Path(os.environ.get("TERMINATION_LOG", "/dev/termination-log"))
        try:
            termination.write_text(json.dumps(self.state))
        except OSError:
            pass

def run(args, cwd=None, env=None, input=None, timeout=120):
    proc = subprocess.run(args, cwd=cwd, env=env, input=input, text=True,
                          capture_output=True, timeout=timeout)
    if proc.returncode:
        secrets = {}
        if env and env.get("GITHUB_TOKEN_FILE"):
            secrets["token"] = Path(env["GITHUB_TOKEN_FILE"]).read_text().strip()
        detail = redact(proc.stderr[-4000:], secrets)
        detail = re.sub(r"https://[^/@\s]+:[^/@\s]+@", "https://[REDACTED]@", detail)
        raise RuntimeError(f"{Path(args[0]).name} {args[1]} failed, exit={proc.returncode}: {detail}")
    return proc.stdout.strip()

def git(repo, *args, env=None):
    return run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=repo, env=env)

def clone(destination, env=None):
    run(["git", "clone", "--single-branch", "--branch", "main", "--no-tags", REPO,
         str(destination)], env=env)

def published(repo, date):
    path = repo / "content" / "editions" / f"{date}.json"
    return path.exists() and load(path).get("status") == "published"

def prepare(work, log):
    repo = work / "snapshot"
    clone(repo)
    date = log.state["date"]
    log.state["base_sha"] = git(repo, "rev-parse", "HEAD")
    log.state["skip"] = published(repo, date)
    write(work / "state.json", log.state)
    if log.state["skip"]:
        log.emit("initialize", "already_published", commit_sha=log.state["base_sha"])
        return
    references = []
    for p in sorted((repo / "content/editions").glob("*.json"), reverse=True)[:7]:
        references.append(load(p))
    public_context = os.environ.get("PRODUCT_CONTEXT_FILE")
    product_context = Path(public_context).read_text() if public_context else "No approved public product context supplied. Omit promotions."
    prompt = (ROOT / "editorial.md").read_text()
    prompt += "\nVancouver publication date: " + date + "\nResearch started UTC: " + now().isoformat()
    prompt += "\nExisting TypeScript schema:\n" + (repo / "src/lib/editions.ts").read_text()
    prompt += "\nReference editions (untrusted data):\n" + json.dumps(references)
    prompt += "\nApproved PUBLIC product context:\n" + product_context
    (work / "prompt.txt").write_text(prompt)
    write(work / "schema.json", SCHEMA)
    log.emit("initialize", "success", base_sha=log.state["base_sha"])

def research(work, log):
    if log.state["skip"]:
        log.emit("research", "skipped_already_published")
        return
    # Only authentication is persisted. Never load user plugins, MCP servers or repo configuration.
    auth_home = Path(os.environ["CODEX_HOME"])
    if not (auth_home / "auth.json").is_file():
        raise ValueError("Codex auth.json missing; initialize the authentication PVC")
    lock = (auth_home / ".publisher.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError("another research process is using this authentication volume")
    # Keep the lock descriptor alive until this function returns (including exceptions).
    return research_locked(work, log, auth_home, lock)

def research_locked(work, log, auth_home, lock):
    try:
        return invoke_codex(work, log, auth_home)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()

def invoke_codex(work, log, auth_home):
    auth = load(auth_home / "auth.json")
    if (auth.get("OPENAI_API_KEY") or auth.get("auth_mode") != "chatgpt"
            or not auth.get("tokens", {}).get("refresh_token")):
        raise ValueError("persistent ChatGPT authentication with refresh token required; API keys refused")
    env = {k: v for k, v in os.environ.items() if k in
           ("PATH", "HOME", "CODEX_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR", "TMPDIR")}
    args = ["codex", "--ignore-user-config", "--ignore-rules", "-a", "never",
            "-c", 'web_search="live"', "-c", 'forced_login_method="chatgpt"',
            "-c", 'cli_auth_credentials_store="file"', "-c", 'features.shell_tool=false',
            "-c", 'features.remote_plugin=false',
            "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
            "--json", "--output-schema", str(work / "schema.json"),
            "--output-last-message", str(work / "generated.json"), "-"]
    log.emit("research", "started")
    # Keep diagnostics private; stream only event types/status and redacted errors, no model reasoning.
    with tempfile.TemporaryFile(mode="w+") as err, tempfile.TemporaryFile(mode="w+") as out:
        proc = subprocess.Popen(args, cwd=work, env=env, stdin=subprocess.PIPE,
                                stdout=out, stderr=err, text=True, start_new_session=True)
        try:
            proc.communicate((work / "prompt.txt").read_text(), timeout=1800)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise RuntimeError("Codex research timed out after 1800 seconds")
        except BaseException:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            raise
        out.seek(0)
        counts = {}
        errors = []
        for line in out:
            try:
                event = json.loads(line)
                typ = event.get("type", "unknown")
                counts[typ] = counts.get(typ, 0) + 1
                if typ in ("error", "turn.failed"):
                    errors.append(redact(json.dumps(event), auth))
            except ValueError:
                pass
        err.seek(0)
        refreshed_auth = load(auth_home / "auth.json")
        stderr = redact(redact(err.read()[-8000:], auth), refreshed_auth)
        errors = [redact(e, refreshed_auth) for e in errors]
        log.emit("research", "process_completed", exit_code=proc.returncode,
                 event_counts=counts, errors=errors, stderr=stderr if proc.returncode else "")
        if proc.returncode or errors or not counts.get("turn.completed"):
            raise RuntimeError("Codex failed; see research process_completed diagnostics")
    if not (work / "generated.json").is_file():
        raise ValueError("Codex produced no JSON file")
    log.emit("research", "success")

def redact(text, auth):
    def strings(value):
        if isinstance(value, dict):
            return [s for v in value.values() for s in strings(v)]
        return [value] if isinstance(value, str) and len(value) > 8 else []
    for secret in strings(auth):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r'(?i)(["\']?(?:access_token|refresh_token|id_token|authorization)["\']?\s*[:=]\s*)["\']?[^\s,"\'}]+', r'\1[REDACTED]', text)
    return re.sub(r"(?i)(bearer\s+|sk-|gh[pousr]_)[A-Za-z0-9_.-]+", "[REDACTED]", text)

# Public URL verifier: fail closed on redirects to private networks or unreachable sources.
def public_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("source URL must be public HTTPS without credentials")
    if parsed.hostname in ("example.com", "example.org", "example.net", "localhost") or parsed.hostname.endswith(".invalid"):
        raise ValueError("placeholder URL")
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("source resolves to nonpublic address")

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def probe(url):
    public_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "DailyEditionPublisher/1.0"})
    with urllib.request.build_opener(SafeRedirect()).open(req, timeout=15) as response:
        if response.status != 200:
            raise ValueError("source HTTP status is not 200")
        response.read(1024)

def strings(value):
    if isinstance(value, dict):
        return " ".join(strings(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(strings(v) for v in value)
    return value if isinstance(value, str) else ""

def validate(payload, date, probe_url=probe, previous=()):
    check(payload, SCHEMA)
    edition = payload["edition"]
    if edition["watchers"]:
        raise ValueError("watchers must be empty; put sourced updates in stories/markets")
    if len(payload["evidence"]) > 60:
        raise ValueError("too many source evidence entries")
    if edition["date"] != date:
        raise ValueError("edition date does not match Vancouver execution date")
    if date != today():
        raise ValueError("execution crossed Vancouver midnight; refusing stale publication")
    def nonempty(value):
        if isinstance(value, dict):
            return all(nonempty(v) for v in value.values())
        if isinstance(value, list):
            return all(nonempty(v) for v in value)
        return bool(value.strip()) if isinstance(value, str) else True
    if not nonempty(edition):
        raise ValueError("public edition contains empty strings")
    text = strings(edition)
    if re.search(r"\b(VEQT|TODO|TBD|lorem ipsum|placeholder)\b|<[^>]+>", text, re.I):
        raise ValueError("prohibited, placeholder or HTML content")
    words = len(re.findall(r"\b[\w’'-]+\b", text))
    if not 600 <= words <= 1000:
        raise ValueError(f"edition must contain 600–1000 words; got {words}")
    for temperature in (edition["weather"]["high"], edition["weather"]["low"]):
        if not math.isfinite(temperature) or not -40 <= temperature <= 50:
            raise ValueError("implausible weather temperature")
    omitted = {x["section"].casefold(): x["reason"].strip() for x in payload["omissions"]}
    if any(len(reason) < 15 for reason in omitted.values()):
        raise ValueError("omissions must explain why verification was unavailable")
    for category in ("LOCAL", "TECHNOLOGY", "promotion"):
        if not any(s["section"].upper().startswith(category.upper()) for s in edition["stories"]):
            if not omitted.get(category.casefold()):
                raise ValueError(f"missing content or explicit omission: {category}")
    for category in ("events", "meetups", "dining"):
        if not edition[category] and not omitted.get(category):
            raise ValueError(f"missing content or explicit omission: {category}")
    for measure, pattern in {"TSX": r"tsx", "S&P 500": r"s&p\s*500", "Nasdaq": r"nasdaq",
                             "Bank of Canada": r"bank of canada", "inflation": r"inflation|cpi",
                             "recession": r"recession|yield curve|unemployment"}.items():
        if not any(re.search(pattern, m["name"], re.I) for m in edition["markets"]) and not omitted.get(measure.casefold()):
            raise ValueError(f"market coverage missing: {measure}")
    required = {"weather": (edition["weather"], "weather")}
    for group in ("stories", "markets", "events", "meetups", "dining"):
        for i, item in enumerate(edition[group]):
            kind = {"stories": "news", "markets": "market", "events": "event", "meetups": "meetup", "dining": "dining"}[group]
            if group == "stories" and item["section"].startswith("PROMOTION"):
                kind = "promotion"
            required[f"{group}/{i}"] = (item, kind)
    verified = set()
    links = set()
    publication = dt.date.fromisoformat(date)
    for proof in payload["evidence"]:
        path = proof["path"]
        if path not in required:
            raise ValueError("evidence references unknown public item")
        item, kind = required[path]
        if proof["kind"] != kind:
            raise ValueError(f"{path}: evidence kind mismatch")
        checked = dt.datetime.fromisoformat(proof["checked_at"].replace("Z", "+00:00"))
        if checked.tzinfo is None or not -300 <= (now() - checked).total_seconds() <= 7200:
            raise ValueError(f"{path}: stale or future verification timestamp")
        age = (publication - dt.date.fromisoformat(proof["as_of"])).days
        max_age = {"news": 7, "market": 120, "weather": 1, "event": 366,
                   "meetup": 366, "dining": 30, "promotion": 30}[kind]
        if not 0 <= age <= max_age:
            raise ValueError(f"{path}: obviously stale or future source date")
        if "url" in item and kind not in ("event", "meetup") and item["url"] != proof["url"]:
            raise ValueError(f"{path}: public URL does not match evidence")
        if kind == "market":
            index = bool(re.search(r"tsx|s&p\s*500|nasdaq", item["name"], re.I))
            if index and age > 5:
                raise ValueError(f"{path}: stale equity session")
            if proof["as_of"] not in item["change"]:
                raise ValueError(f"{path}: market as-of date missing from public change")
        links.add(proof["url"])
        if kind in ("weather", "market") and proof["url"] not in strings(item):
            raise ValueError(f"{path}: public item must include source URL")
        if kind in ("event", "meetup"):
            event_date = dt.date.fromisoformat(proof["event_date"])
            if not publication <= event_date <= publication + dt.timedelta(days=366):
                raise ValueError(f"{path}: event is past or too far ahead")
            if not proof["venue"].strip() or proof["venue"] not in item["date"] or proof["event_date"] not in item["date"]:
                raise ValueError(f"{path}: event date/venue missing in public date")
            if not proof["registration_url"] or item["url"] != proof["registration_url"]:
                raise ValueError(f"{path}: registration URL mismatch")
            links.add(proof["registration_url"])
        verified.add(path)
    if verified != set(required):
        raise ValueError("every public item requires evidence")
    days = payload["weather_days"]
    if [d["date"] for d in days] != [(publication + dt.timedelta(days=i)).isoformat() for i in range(4)]:
        raise ValueError("weather must cover today plus three days in order")
    for day in days:
        if not -40 <= day["low"] <= day["high"] <= 50 or day["date"] not in edition["weather"]["summary"]:
            raise ValueError("weather summary needs all four dates and plausible temperatures")
        if day["url"] not in {e["url"] for e in payload["evidence"] if e["kind"] == "weather"}:
            raise ValueError("forecast source must have weather evidence")
        forecast = f'{day["date"]}: {day["high"]:g}/{day["low"]:g}°C'
        if forecast not in edition["weather"]["summary"]:
            raise ValueError("weather summary must include dated high/low°C matching sidecar")
        links.add(day["url"])
    if any(days[0][k] != edition["weather"][k] for k in ("high", "low")):
        raise ValueError("today's weather conflicts with sidecar")
    old = {s.get("url") for e in previous for s in e.get("stories", [])}
    if any(s["url"] in old for s in edition["stories"]):
        raise ValueError("repeated story URL: use a fresh source for a material update")
    if len(links) > 60:
        raise ValueError("too many source URLs")
    for url in sorted(links):
        probe_url(url)
    return edition

def credential_env(work):
    token_path = Path(os.environ.get("GITHUB_TOKEN_FILE", "/github/token"))
    if not token_path.is_file() or not token_path.read_text().strip():
        raise ValueError("GitHub credential file missing or empty")
    # Askpass reads the mounted file at invocation time. Token never enters git URLs/argv/logs.
    askpass = work / "askpass.py"
    askpass.write_text("#!/usr/bin/env python3\nimport os,sys\nfrom pathlib import Path\nprint('x-access-token' if 'Username' in sys.argv[1] else Path(os.environ['GITHUB_TOKEN_FILE']).read_text().strip())\n")
    askpass.chmod(0o700)
    env = os.environ.copy()
    env.update(GIT_ASKPASS=str(askpass), GIT_TERMINAL_PROMPT="0", GITHUB_TOKEN_FILE=str(token_path),
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    return env

def readback(repo, sha, date, env):
    git(repo, "fetch", "origin", "main", env=env)
    git(repo, "merge-base", "--is-ancestor", sha, "origin/main", env=env)
    raw = git(repo, "show", f"origin/main:content/editions/{date}.json", env=env)
    result = json.loads(raw)
    check(result, EDITION)
    if result["date"] != date or result["status"] != "published":
        raise ValueError("read-back date/status mismatch")
    return result

def deploy_status(sha, log):
    token = Path(os.environ.get("GITHUB_TOKEN_FILE", "/github/token")).read_text().strip()
    for attempt in range(60):
        req = urllib.request.Request(f"{API}/actions/runs?head_sha={sha}&per_page=100",
                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as response:
            runs = json.load(response)["workflow_runs"]
        matches = [r for r in runs if r["path"] == ".github/workflows/deploy.yml" and r["head_sha"] == sha]
        if matches:
            latest = max(matches, key=lambda r: r["id"])
            if latest["status"] == "completed":
                log.emit("verification", "pages_workflow_completed", conclusion=latest["conclusion"], url=latest["html_url"])
                if latest["conclusion"] != "success":
                    raise RuntimeError("GitHub Pages workflow did not succeed")
                return
        time.sleep(10)
    raise RuntimeError("GitHub Pages workflow verification timed out")

def publish(work, log):
    date = log.state["date"]
    if date != today():
        raise ValueError("Vancouver date changed; rerun with today's date")
    log.stage = "publishing"
    repo = work / "publish-repo"
    env = credential_env(work)
    clone(repo, env)
    if published(repo, date):
        log.stage = "verification"
        sha = git(repo, "rev-parse", "HEAD")
        result = readback(repo, sha, date, env)
        log.emit("verification", "already_published", commit_sha=sha, date=result["date"])
        log.state["commit_sha"] = sha
        if os.environ.get("VERIFY_PAGES") == "1":
            # Find the edition's commit, rather than an unrelated later commit.
            edition_sha = git(repo, "log", "-1", "--format=%H", "--", f"content/editions/{date}.json")
            deploy_status(edition_sha, log)
        return "already_published"
    if log.state["skip"]:
        raise ValueError("published edition disappeared after initialization; manual investigation required")
    log.stage = "validation"
    previous = [load(p) for p in sorted((repo / "content/editions").glob("*.json"), reverse=True)[:7]
                if p.stem != date]
    if (work / "generated.json").stat().st_size > 1_000_000:
        raise ValueError("generated JSON exceeds 1 MB limit")
    edition = validate(load(work / "generated.json"), date, previous=previous)
    log.emit("validation", "success", sha256=hashlib.sha256(json.dumps(edition, sort_keys=True).encode()).hexdigest())
    log.stage = "publishing"
    path = f"content/editions/{date}.json"
    for attempt in range(3):
        if date != today():
            raise ValueError("Vancouver midnight reached before push")
        base = git(repo, "rev-parse", "HEAD")
        write(repo / path, edition)
        git(repo, "add", "--", path)
        staged = git(repo, "diff", "--cached", "--name-only").splitlines()
        if staged != [path]:
            raise ValueError("staging guard rejected unexpected change")
        git(repo, "-c", "user.name=Daily Edition Publisher", "-c", "user.email=daily-edition@users.noreply.github.com",
            "commit", "-m", f"Publish edition {date}")
        sha = git(repo, "rev-parse", "HEAD")
        try:
            git(repo, "push", "origin", "HEAD:refs/heads/main", env=env)
            log.emit("publishing", "push_success", commit_sha=sha)
            break
        except RuntimeError as exc:
            log.emit("publishing", "push_failed", attempt=attempt + 1, candidate_sha=sha, error=str(exc))
            git(repo, "fetch", "origin", "main", env=env)
            remote = git(repo, "rev-parse", "origin/main")
            # Push can succeed remotely even if its response was lost.
            try:
                git(repo, "merge-base", "--is-ancestor", sha, "origin/main")
                break
            except RuntimeError:
                pass
            if remote == base:
                raise RuntimeError("GitHub push rejected without concurrent branch update (credential/rule/transport error)")
            # Destructive only in disposable work directory, never a user checkout.
            git(repo, "reset", "--hard", "origin/main")
            if published(repo, date):
                result = readback(repo, remote, date, env)
                log.emit("verification", "concurrent_publication_won", commit_sha=remote, date=result["date"])
                log.state["commit_sha"] = remote
                if os.environ.get("VERIFY_PAGES") == "1":
                    deploy_status(git(repo, "log", "-1", "--format=%H", "--", path), log)
                return "already_published"
            previous = [load(p) for p in sorted((repo / "content/editions").glob("*.json"), reverse=True)[:7] if p.stem != date]
            validate(load(work / "generated.json"), date, previous=previous)
            if attempt == 2:
                raise RuntimeError("concurrent update retry limit exceeded")
    log.state["commit_sha"] = sha
    write(work / "state.json", log.state)
    log.stage = "verification"
    log.emit("verification", "started", commit_sha=sha)
    if readback(repo, sha, date, env) != edition:
        raise ValueError("committed JSON differs from validated edition")
    log.emit("verification", "success", commit_sha=sha)
    if os.environ.get("VERIFY_PAGES") == "1":
        deploy_status(sha, log)
    return "published"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["prepare", "research", "publish"])
    parser.add_argument("--work", type=Path, default=Path("/work"))
    args = parser.parse_args()
    args.work = args.work.resolve()
    os.umask(0o077)
    args.work.mkdir(parents=True, exist_ok=True)
    if args.stage == "prepare":
        write(args.work / "state.json", dict(execution_id=str(uuid.uuid4()), start=now().isoformat(),
              date=today(), status="running", exit_code=None))
    log = Log(args.work)
    log.stage = {"prepare": "initialize", "research": "research", "publish": "publishing"}[args.stage]
    log.emit(log.stage, "started")
    def stopped(signum, frame):
        raise RuntimeError(f"terminated by signal {signum}")
    signal.signal(signal.SIGTERM, stopped)
    try:
        if args.stage == "prepare":
            prepare(args.work, log)
        elif args.stage == "research":
            research(args.work, log)
        else:
            result = publish(args.work, log)
            log.finish("success", 0, result=result, commit_sha=log.state.get("commit_sha"))
        return 0
    except Exception as exc:
        log.emit(log.stage, "failure", error_type=type(exc).__name__, error=str(exc))
        log.finish("failure", 1, failed_stage=log.stage)
        return 1

if __name__ == "__main__":
    sys.exit(main())
