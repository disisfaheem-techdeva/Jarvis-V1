#!/usr/bin/env python3
"""API Manager test bench runner (founder console > API Manager > Test bench).
Cron, every minute, root (one run at a time):

  * * * * * /usr/bin/python3 /opt/techdeva-platform/techdeva-platform/ops/agent-template/model_bench.py >> /var/log/techdeva-bench.log 2>&1

Takes one queued test from the platform (python -m app.ai_keys bench-next),
builds a COPY of that agent's brain in a temporary folder (SOUL.md, config,
memories, skills; never its WhatsApp session, chats or logins), points it at
the chosen key and model (ai_key_config.py, as a real switch would), and asks
each question with `hermes chat -Q -q` in a throwaway container of the agent's
own image. Nothing is sent on WhatsApp. Answers, times and errors go back with
`bench-done`. The key lives only in a mode-600 env file inside the temporary
folder, which is removed at the end; it is never printed or logged.

An answer passes when it is not empty, is not an error, shows no <think>
block and came within 90 seconds.
"""
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PLATFORM = os.path.abspath(os.path.join(HERE, "..", ".."))
PROTECTED = re.compile(r"^(irfan$|clinic$|salon$|hermes-workspace|workspace|.*ofsd)")
COPY = ("SOUL.md", "config.yaml", "memories", "skills", "home", ".skills_prompt_snapshot.json")
SLOW_MS = 90000


def log(msg):
    print("%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg), flush=True)


def pyx(args, stdin=None):
    return subprocess.run(["docker", "compose", "exec", "-T", "api", "python", "-m", "app.ai_keys"] + args,
                          cwd=PLATFORM, input=stdin, capture_output=True, text=True, timeout=120)


def done(tid, results, error=None):
    pyx(["bench-done", tid], json.dumps({"results": results, "error": error}))


def run(job):
    agent = job["agent_key"]
    if PROTECTED.match(agent) or not re.fullmatch(r"[a-z0-9-]{3,60}", agent):
        return [], "this agent is not tested here"
    vol = subprocess.run(["docker", "volume", "inspect", "-f", "{{.Mountpoint}}", "hermes-%s-data" % agent],
                         capture_output=True, text=True).stdout.strip()
    image = subprocess.run(["docker", "inspect", "-f", "{{.Config.Image}}", "hermes-agent-%s" % agent],
                           capture_output=True, text=True).stdout.strip()
    if not vol or not image or not os.path.isfile(os.path.join(vol, "config.yaml")):
        return [], "the agent's files or image were not found"
    st = os.stat(os.path.join(vol, "config.yaml"))
    sbx = tempfile.mkdtemp(prefix="td-bench-", dir="/root")
    try:
        data = os.path.join(sbx, "data")
        os.makedirs(data)
        for name in COPY:
            src = os.path.join(vol, name)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(data, name), symlinks=True)
            elif os.path.isfile(src):
                shutil.copy2(src, os.path.join(data, name))
        cfg = os.path.join(data, "config.yaml")
        env = {}
        try:
            for line in open("/docker/hermes-%s/agent.env" % agent, encoding="utf-8"):
                if line.startswith("GEMINI_API_KEY="):
                    env["GEMINI_API_KEY"] = line.split("=", 1)[1].strip()
        except OSError:
            pass
        if job["provider"] == "gemini":
            env["GEMINI_API_KEY"] = job["key"]
            saved = "/docker/hermes-%s/ai-key-gemini-model.json" % agent
            if os.path.exists(saved):            # the agent is on another provider now: test its Gemini chain
                shutil.copy2(saved, os.path.join(sbx, "saved.json"))
                subprocess.run([sys.executable, os.path.join(HERE, "ai_key_config.py"), cfg,
                                os.path.join(sbx, "saved.json"), "gemini"], check=True, capture_output=True)
        else:
            env["TD_AI_KEY"] = job["key"]
            subprocess.run([sys.executable, os.path.join(HERE, "ai_key_config.py"), cfg, os.path.join(sbx, "saved.json"),
                            "custom", job["base_url"], job["model"]], check=True, capture_output=True)
        envfile = os.path.join(sbx, "env")
        fd = os.open(envfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            for k, v in env.items():
                f.write("%s=%s\n" % (k, v))
        subprocess.run(["chown", "-R", "%d:%d" % (st.st_uid, st.st_gid), data], check=True)
        results = []
        for q in job["questions"]:
            t0 = time.monotonic()
            try:
                p = subprocess.run(["docker", "run", "--rm", "--user", "%d:%d" % (st.st_uid, st.st_gid),
                                    "--env-file", envfile, "-e", "HERMES_HOME=/opt/data", "-v", "%s:/opt/data" % data,
                                    "--entrypoint", "hermes", image, "chat", "-Q", "-q", q],
                                   capture_output=True, text=True, timeout=150)
                out = p.stdout
            except subprocess.TimeoutExpired:
                out = "Error: no answer within 150 seconds"
            ms = int((time.monotonic() - t0) * 1000)
            lines = [l for l in out.splitlines() if l.strip() and not l.startswith("session_id:") and "tirith" not in l]
            answer = "\n".join(lines).strip()
            err = None
            if not answer:
                err = "no answer"
            elif re.match(r"^(Error:|API call failed)", answer):
                err = answer[:300]
            elif "<think>" in answer:
                err = "the model's private thinking would reach the customer"
            elif ms > SLOW_MS:
                err = "too slow (%d s)" % (ms // 1000)
            results.append({"q": q, "a": None if err and not answer else answer[:1500], "ms": ms, "ok": err is None,
                            "error": err})
        return results, None
    finally:
        shutil.rmtree(sbx, ignore_errors=True)


def main():
    lock = open("/run/techdeva-bench.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0
    p = pyx(["bench-next"])
    line = (p.stdout or "").strip()
    if not line:
        return 0
    job = json.loads(line)
    log("bench %s on %s (%s %s): %d questions" % (job["id"][:8], job["agent_key"], job["provider"],
                                                   job.get("model") or "", len(job["questions"])))
    try:
        results, error = run(job)
    except Exception as e:                       # never leave a test "running"
        results, error = [], "%s: %s" % (type(e).__name__, str(e)[:200])
    job.pop("key", None)
    done(job["id"], results, error)
    log("bench %s: %s" % (job["id"][:8], error or "%d/%d passed" % (sum(1 for r in results if r["ok"]), len(results))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
