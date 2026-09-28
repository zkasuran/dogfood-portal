#!/usr/bin/env python3
"""Record the DOGFOOD portal demo video, end to end, against a live portal.

Every screen in the video is real: the portal is booted fresh from
spec/fixtures.json, the browser scenes are driven through the actual UI, and the
terminal scenes run the exact commands they show and print their real output.
Nothing is mocked or pre-rendered.

    pip install -r requirements.txt playwright imageio-ffmpeg
    playwright install chromium
    python3 demo/record_demo.py            # writes demo/dogfood-demo.mp4

Options: --workdir (scratch dir for db, key, raw recording), --port, --out.
"""
import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src")
W, H = 1280, 720
RECAP_SECS = 8

TOKENS = {
    "ORG": "organizer0000000000000000000000000000000",
    "JUDGE_A": "judgea0000000000000000000000000000000000",
    "JUDGE_B": "judgeb0000000000000000000000000000000000",
    "PARTICIPANT": "participant00000000000000000000000000000",
}

# --------------------------------------------------------------------------- boot


def boot_portal(workdir, port):
    """Fresh database, fresh signing key, seeded from the shared fixtures, served by
    gunicorn exactly as entrypoint.sh does. Returns (process, seed_output)."""
    for name in ("db.sqlite3", "signing_key.pem"):
        path = os.path.join(workdir, name)
        if os.path.exists(path):
            os.remove(path)
    env = dict(os.environ)
    env.update(
        DJANGO_DB_PATH=os.path.join(workdir, "db.sqlite3"),
        DOGFOOD_SIGNING_KEY_PATH=os.path.join(workdir, "signing_key.pem"),
        GIT_COMMIT=subprocess.check_output(
            ["git", "-C", REPO, "rev-parse", "--short", "HEAD"], text=True
        ).strip(),
    )
    py = sys.executable
    subprocess.run([py, "manage.py", "migrate", "--noinput"], cwd=SRC, env=env,
                   check=True, capture_output=True)
    subprocess.run([py, "manage.py", "collectstatic", "--noinput"], cwd=SRC, env=env,
                   check=True, capture_output=True)
    seed = subprocess.run([py, "manage.py", "seed"], cwd=SRC, env=env, check=True,
                          capture_output=True, text=True).stdout
    gunicorn = os.path.join(os.path.dirname(py), "gunicorn")
    proc = subprocess.Popen(
        [gunicorn, "dogfood.wsgi:application", "--bind", f"127.0.0.1:{port}",
         "--workers", "2", "--error-logfile", os.path.join(workdir, "server.log")],
        cwd=SRC, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            break
        except Exception:
            time.sleep(0.25)
    else:
        proc.kill()
        raise SystemExit("portal did not come up")
    return proc, seed


# ------------------------------------------------------------------------ visuals

BASE_CSS = """
html,body{margin:0;height:100%;background:#0f1a14;font-family:'Noto Sans',sans-serif}
.card{height:100%;display:flex;flex-direction:column;justify-content:center;
      padding:0 110px;color:#eef4ef;box-sizing:border-box}
.kicker{color:#7fc79a;font-size:22px;letter-spacing:4px;text-transform:uppercase}
h1{font-size:64px;margin:18px 0 10px}
.sub{font-size:26px;color:#b9cbbf;line-height:1.5;max-width:1000px}
.steps{margin-top:36px;display:flex;gap:14px;font-size:20px}
.steps span{padding:8px 18px;border-radius:20px;background:#1d2f24;color:#8aa596}
.steps span.on{background:#2f6b4c;color:#fff}
.term{height:100%;box-sizing:border-box;padding:34px 44px;background:#0d1117;
      color:#d6e2da;font:17px/1.45 'DejaVu Sans Mono','Noto Sans Mono',monospace;
      overflow:hidden}
.term .bar{color:#7fc79a;font:600 15px 'Noto Sans',sans-serif;letter-spacing:2px;
      text-transform:uppercase;margin-bottom:18px}
.term pre{margin:0;white-space:pre-wrap;word-break:break-all}
.p{color:#7fc79a}.c{color:#fff;font-weight:bold}.ok{color:#56d364}.bad{color:#ff7b72}
.dim{color:#8b949e}
"""

OVERLAY_JS = r"""
(() => {
  if (window.__demoOverlay) return;
  window.__demoOverlay = true;
  const add = () => {
    const cur = document.createElement('div');
    cur.id = '__cursor';
    cur.style.cssText = 'position:fixed;z-index:2147483647;width:18px;height:18px;'
      + 'border-radius:50%;background:rgba(255,196,0,.85);border:2px solid #fff;'
      + 'box-shadow:0 0 6px rgba(0,0,0,.5);pointer-events:none;left:-40px;top:-40px;'
      + 'transform:translate(-50%,-50%);transition:left .25s,top .25s';
    document.body.appendChild(cur);
    addEventListener('mousemove', e => {cur.style.left=e.clientX+'px';cur.style.top=e.clientY+'px';}, true);
    addEventListener('mousedown', () => {cur.style.background='rgba(255,90,0,.95)';}, true);
    addEventListener('mouseup', () => {cur.style.background='rgba(255,196,0,.85)';}, true);
    const cap = document.createElement('div');
    cap.id = '__caption';
    cap.style.cssText = 'position:fixed;z-index:2147483646;left:50%;bottom:26px;'
      + 'transform:translateX(-50%);max-width:1080px;padding:12px 24px;border-radius:10px;'
      + 'background:rgba(10,20,14,.88);color:#fff;font:500 21px/1.4 "Noto Sans",sans-serif;'
      + 'text-align:center;box-shadow:0 4px 18px rgba(0,0,0,.35);display:none';
    document.body.appendChild(cap);
    // Admin forms run to the bottom of the screen, so captions sit over the admin
    // header there instead of over the fields being filled in.
    window.__placeCaption = () => {
      const c = document.getElementById('__caption');
      if (!c) return;
      const top = location.pathname.startsWith('/admin')
        && !document.querySelector('.term, .card');
      c.style.top = top ? '4px' : 'auto';
      c.style.bottom = top ? 'auto' : '26px';
    };
    window.__placeCaption();
    let saved = '';
    try { saved = sessionStorage.getItem('__caption'); } catch (e) {}
    if (saved) { cap.textContent = saved; cap.style.display = 'block'; }
  };
  if (document.body) add(); else addEventListener('DOMContentLoaded', add);
})();
"""


class Demo:
    def __init__(self, page, base, shell_env, workdir):
        self.page = page
        self.base = base
        self.env = shell_env
        self.workdir = workdir

    def hold(self, secs):
        self.page.wait_for_timeout(int(secs * 1000))

    # browser helpers
    def caption(self, text, secs=0):
        self.page.evaluate(
            """t => { try { sessionStorage.setItem('__caption', t || ''); } catch (e) {}
                      if (!document.getElementById('__caption')) {
                        const cap = document.createElement('div'); cap.id = '__caption';
                        cap.style.cssText = 'position:fixed;z-index:2147483646;left:50%;bottom:26px;'
                          + 'transform:translateX(-50%);max-width:1080px;padding:12px 24px;'
                          + 'border-radius:10px;background:rgba(10,20,14,.88);color:#fff;'
                          + 'font:500 21px/1.4 "Noto Sans",sans-serif;text-align:center;'
                          + 'box-shadow:0 4px 18px rgba(0,0,0,.35);display:none';
                        document.body.appendChild(cap);
                      }
                      const c = document.getElementById('__caption');
                      if (c) { c.textContent = t; c.style.display = t ? 'block' : 'none'; }
                      window.__placeCaption && window.__placeCaption(); }""",
            text,
        )
        if secs:
            self.hold(secs)

    def goto(self, path, caption=None, secs=0):
        self.page.goto(self.base + path)
        self.page.wait_for_load_state("networkidle")
        if caption is not None:
            self.caption(caption)
        if secs:
            self.hold(secs)

    def type_into(self, selector, text, delay=45):
        self.page.click(selector)
        self.page.fill(selector, "")
        self.page.type(selector, text, delay=delay)

    def click(self, selector, pause=0.6):
        loc = self.page.locator(selector).first
        loc.scroll_into_view_if_needed()
        loc.hover()
        self.hold(0.35)
        loc.click()
        if pause:
            self.hold(pause)

    def select(self, selector, label, pause=0.5):
        self.page.locator(selector).first.hover()
        self.hold(0.3)
        self.page.select_option(selector, label=label)
        self.hold(pause)

    # full-screen cards
    def card(self, kicker, title, sub, secs, step=None):
        steps = ""
        if step is not None:
            names = ["Create", "Submit", "Judge", "Publish", "Verify"]
            steps = '<div class="steps">' + "".join(
                f'<span class="{"on" if i == step else ""}">{n}</span>'
                for i, n in enumerate(names)
            ) + "</div>"
        self.page.set_content(
            f"<style>{BASE_CSS}</style><div class='card'><div class='kicker'>{kicker}</div>"
            f"<h1>{title}</h1><div class='sub'>{sub}</div>{steps}</div>"
        )
        self.caption("")
        self.hold(secs)

    # terminal scenes: run for real, then replay the real output on screen
    def run(self, cmd):
        out = subprocess.run(
            ["sh", "-c", cmd], cwd=self.workdir, env=self.env,
            capture_output=True, text=True,
        )
        return (out.stdout + out.stderr).rstrip("\n")

    def terminal(self, label, steps, caption=None, tail=3.0):
        """steps: list of (command, seconds_to_hold_after_output)."""
        self.page.set_content(
            f"<style>{BASE_CSS}</style><div class='term'><div class='bar'>{html.escape(label)}"
            "</div><pre id='t'></pre></div>"
        )
        if caption:
            self.caption(caption)
        for cmd, after in steps:
            output = self.run(cmd)
            self.page.evaluate(
                """([cmd]) => new Promise(done => {
                    const t = document.getElementById('t');
                    const p = document.createElement('span'); p.className='p'; p.textContent='$ ';
                    const c = document.createElement('span'); c.className='c';
                    t.appendChild(p); t.appendChild(c);
                    let i = 0;
                    const tick = () => { c.textContent = cmd.slice(0, ++i);
                      if (i < cmd.length) setTimeout(tick, 22); else done(); };
                    tick();
                })""",
                [cmd],
            )
            self.hold(0.5)
            self.page.evaluate(
                """([out]) => new Promise(done => {
                    const t = document.getElementById('t');
                    t.appendChild(document.createTextNode('\\n'));
                    const lines = out ? out.split('\\n') : [];
                    let i = 0;
                    const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;');
                    const paint = s => {
                      const e = esc(s);
                      if (e.includes('NOT VERIFIED'))
                        return e.replace('NOT VERIFIED', '<span class="bad">NOT VERIFIED</span>');
                      return e
                        .replace(/\\b(FAIL|HTTP\\/1\\.1 4\\d\\d.*)/g, '<span class="bad">$1</span>')
                        .replace(/\\b(PASS|VERIFIED|HTTP\\/1\\.1 20\\d.*)/g, '<span class="ok">$1</span>');
                    };
                    const step = () => {
                      if (i >= lines.length) { t.appendChild(document.createTextNode('\\n'));
                        const pre = t.parentElement; pre.scrollTop = pre.scrollHeight; done(); return; }
                      const s = document.createElement('span');
                      s.innerHTML = paint(lines[i++]) + '\\n';
                      t.appendChild(s);
                      const box = t.parentElement;
                      while (box.scrollHeight > box.clientHeight && t.firstChild) t.removeChild(t.firstChild);
                      setTimeout(step, 28);
                    };
                    step();
                })""",
                [output],
            )
            self.hold(after)
        self.hold(tail)


# -------------------------------------------------------------------------- script


def script(d, seed_output):
    port = d.base.rsplit(":", 1)[1]
    B = d.base

    # ---------------------------------------------------------------- intro
    d.card("", "", "", 1.5)  # lead-in, the recorder can drop the first frames
    d.card("DOGFOOD 2026", "DOGFOOD portal",
           "Self-hostable hackathon submission and judging, with <b>reproducible "
           "signed results</b>: anyone can recompute the winner and check the "
           "signature without trusting the host.", 7)
    d.card("One full event lifecycle", "What you will see",
           "The organizer creates an event, a team's project is submitted, a judge "
           "scores it, the organizer publishes, and a standalone verifier proves the "
           "result, then rejects a tampered copy. Every screen is the live portal.",
           7, step=None)

    # ---------------------------------------------------------------- boot
    boot_lines = "\n".join(seed_output.strip().splitlines()[:9])
    with open(os.path.join(d.workdir, "seed-output.txt"), "w") as fh:
        fh.write(boot_lines + "\n")
    d.terminal(
        "one container, network off",
        [
            ("cat seed-output.txt   # what `docker compose up` prints on first boot", 4),
            (f"curl -s {B}/health", 2.5),
            (f"curl -s {B}/api/projects | python3 -c 'import json,sys; "
             "print(len(json.load(sys.stdin)), \"projects loaded from spec/fixtures.json\")'", 3),
        ],
        caption="Boots with the shared fixtures loaded and one seed account per role.",
    )

    # ---------------------------------------------------------------- CREATE
    d.card("Step 1", "Create", "The organizer sets up an event, a team and the "
           "weighted rubric in the organizer console.", 4, step=0)
    d.goto("/", caption="The public home page. The organizer console is one click away.", secs=3)
    d.click("text=Organizer console")
    d.page.wait_for_load_state("networkidle")
    d.caption("Organizer signs in (seed login organizer / organizer).")
    d.type_into("#id_username", "organizer", delay=70)
    d.type_into("#id_password", "organizer", delay=70)
    d.click("input[type=submit]", pause=1.2)
    d.caption("The organizer console: every core table is browsable here.", 3)

    d.goto("/admin/core/event/add/", caption="Create a new event with its submission deadline.")
    d.type_into("#id_external_id", "evt_demo")
    d.type_into("#id_name", "DOGFOOD Demo Finals")
    d.type_into("#id_submissions_close_0", "2026-12-31")
    d.type_into("#id_submissions_close_1", "18:00:00")
    d.type_into("#id_starts_at_0", "2026-12-01")
    d.type_into("#id_starts_at_1", "18:00:00")
    d.type_into("#id_ends_at_0", "2026-12-31")
    d.type_into("#id_ends_at_1", "18:00:00")
    d.hold(1)
    d.click("input[name=_save]", pause=1.0)
    d.caption("Saved. The fixture event, Sample Hack 2026, sits alongside it.", 3.5)

    d.goto("/admin/core/team/add/", caption="Register a team for the new event.")
    d.type_into("#id_external_id", "team_demo")
    d.select("#id_event", "DOGFOOD Demo Finals")
    d.type_into("#id_name", "Demo Crew")
    d.click("input[name=_save]", pause=1.0)
    d.caption("Team created.", 2)

    d.goto("/admin/core/rubric/1/change/",
           caption="The rubric: each criterion carries a weight the organizer controls.", secs=4)
    d.caption("These weights go into the signed results bundle, so anyone can see exactly "
              "how scores were combined.", 4)

    # ---------------------------------------------------------------- SUBMIT
    d.card("Step 2", "Submit", "Projects land in a public gallery. Deadlines are enforced "
           "by the backend, not by hiding a button.", 4, step=1)
    d.goto("/projects", caption="The public gallery: 41 fixture projects, no login needed.", secs=3.5)
    d.type_into("input[name=q]", "Signal", delay=90)
    d.click("button[type=submit]", pause=1.0)
    d.caption("Search over title and summary.", 3)

    d.terminal(
        "the deadline is enforced in the API",
        [
            (f"curl -si -X POST {B}/api/projects -H \"Authorization: Token $PARTICIPANT\" "
             "-H 'Content-Type: application/json' -d '{\"title\":\"Late entry\"}' "
             "| sed -n '1p;$p'", 5),
        ],
        caption="A participant submitting after Sample Hack 2026 closed gets a 403, "
                "and the refusal is written to the audit log.",
    )
    d.goto("/admin/core/auditlog/", caption="The refused submission, in the audit log.", secs=4)

    d.goto("/admin/core/project/add/",
           caption="In this build a new entry is recorded through the organizer console.")
    d.type_into("#id_external_id", "prj_demo")
    d.select("#id_team", "Demo Crew")
    d.select("#id_track", "Developer tools")
    d.type_into("#id_title", "Demo Lantern")
    d.type_into("#id_summary", "Lights up the judging trail from raw score to winner.")
    d.type_into("#id_repo_url", "https://example.org/repo/demo-lantern")
    d.select("#id_status", "Submitted")
    d.type_into("#id_submitted_at_0", "2026-12-10")
    d.type_into("#id_submitted_at_1", "12:00:00")
    d.click("input[name=_save]", pause=1.0)
    d.caption("Submitted.", 2)
    d.goto("/projects?q=Lantern", caption="Demo Lantern is now in the public gallery.", secs=4)

    # ---------------------------------------------------------------- JUDGE
    d.card("Step 3", "Judge", "A judge scores the project on the weighted rubric. "
           "Judges can read their own scores and nobody else's.", 4, step=2)
    d.goto("/admin/core/score/add/",
           caption="Judge jdg_07's score for Demo Lantern, one value per criterion (1 to 5).")
    d.select("#id_judge", "jdg_07 (judge)")
    d.select("#id_project", "Demo Lantern")
    d.type_into("#id_comment", "Clear trail from raw score to rank. Solid build.", delay=30)
    for i, (label, value) in enumerate(
        [("Functionality (w=0.400)", "5"), ("Quality (w=0.400)", "5"),
         ("Innovation (w=0.200)", "4")]
    ):
        d.click("text=Add another Score value", pause=0.4)
        d.select(f"#id_values-{i}-criterion", label, pause=0.2)
        d.type_into(f"#id_values-{i}-value", value, delay=80)
    d.hold(1)
    d.click("input[name=_save]", pause=1.0)
    d.caption("Score saved.", 2)

    d.terminal(
        "role isolation lives in the backend",
        [
            (f"curl -s {B}/api/judge/scores -H \"Authorization: Token $JUDGE_A\" "
             "| python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[\"judge_id\"], "
             "d[\"count\"], \"scores\"); [print(\"  \", s[\"project\"], s[\"project_title\"], "
             "s[\"criteria\"]) for s in d[\"scores\"]]'", 4),
            (f"curl -s -w '  -> HTTP %{{http_code}}\\n' '{B}/api/judge/scores?judge_id=jdg_07' "
             "-H \"Authorization: Token $JUDGE_B\"", 4),
            (f"curl -s -w '  -> HTTP %{{http_code}}\\n' {B}/api/judge/scores "
             "-H \"Authorization: Token $PARTICIPANT\"", 3),
            (f"curl -s {B}/api/progress -H \"Authorization: Token $ORG\"", 4),
        ],
        caption="Judge A sees their own four scores, including Demo Lantern. Judge B asking "
                "for Judge A's scores is refused 403, as is a participant.",
    )
    d.terminal(
        "the official DOGFOOD acceptance checker",
        [("python3 spec/run.py demo.dogfood.toml", 6)],
        caption="The unmodified acceptance checker against this running portal: "
                "7 of 7 checks pass for T1 and T2.",
    )

    # ---------------------------------------------------------------- PUBLISH
    d.card("Step 4", "Publish", "Results stay hidden until the organizer publishes. "
           "Publishing and signing are one action.", 4, step=3)
    d.terminal(
        "results are hidden, and only an organizer can publish",
        [
            (f"curl -s {B}/api/results", 2.5),
            (f"curl -s -w '  -> HTTP %{{http_code}}\\n' {B}/api/results/bundle", 2.5),
            (f"curl -s -w '  -> HTTP %{{http_code}}\\n' -X POST {B}/api/results/publish "
             "-H \"Authorization: Token $JUDGE_A\"", 3),
            (f"curl -s -X POST {B}/api/results/publish -H \"Authorization: Token $ORG\" "
             "| python3 -c 'import json,sys; b=json.load(sys.stdin); p=b[\"payload\"]; "
             "print(\"published, method:\", p[\"method\"]); print(\"digest:   \", b[\"digest\"]); "
             "print(\"signature:\", b[\"signature\"][:64] + \"...\"); "
             "print(\"commit:   \", p.get(\"code_commit\"))'", 5),
        ],
        caption="Before publishing the public sees nothing and a judge's publish is refused. "
                "The organizer's publish returns a signed bundle.",
    )
    d.terminal(
        "the published ranking, now public",
        [
            (f"curl -s {B}/api/results | python3 -c 'import json,sys; r=json.load(sys.stdin); "
             "print(\"published:\", r[\"published\"], \"| method:\", r[\"method\"]); "
             "[print(\"  #%-2d %-9s final=%.3f n=%d\" % (x[\"rank\"], x[\"project\"], x[\"final\"], "
             "x[\"n\"])) for x in r[\"ranking\"][:8]]'", 4),
            (f"curl -s {B}/api/export/results.csv -H \"Authorization: Token $ORG\" | head -n 5", 5),
        ],
        caption="The ranking and a CSV export for the organizer. Scores are adjusted for "
                "harsh and generous judges, as documented in JUDGING.md.",
    )

    # ---------------------------------------------------------------- VERIFY
    d.card("Step 5", "Verify", "Anyone downloads the bundle and checks it with the "
           "standalone verify.py. No account, no trust in the host.", 4, step=4)
    d.terminal(
        "verify the real bundle",
        [
            (f"curl -s {B}/api/results/bundle -o bundle.json && python3 -c 'import json; "
             "p=json.load(open(\"bundle.json\"))[\"payload\"]; print(len(p[\"raw_scores\"]), "
             "\"raw scores |\", \"weights:\", p[\"rubric\"][\"weights\"], \"| method:\", p[\"method\"])'", 4),
            (f"curl -s {B}/api/verification-key", 3),
            ("python3 verify.py bundle.json", 6),
        ],
        caption="verify.py recomputes the ranking from the raw scores, checks the digest and "
                "verifies the Ed25519 signature.",
    )
    d.terminal(
        "now tamper with one score",
        [
            ("python3 -c 'import json; b=json.load(open(\"bundle.json\")); "
             "s=next(r for r in b[\"payload\"][\"raw_scores\"] if r[\"project\"]!=\"prj_demo\" and min(r[\"criteria\"].values())<5); "
             "print(\"changing\", s[\"judge\"], \"on\", s[\"project\"], s[\"criteria\"], "
             "\"-> all 5s\"); s[\"criteria\"]={k:5 for k in s[\"criteria\"]}; "
             "json.dump(b, open(\"tampered.json\",\"w\"))'", 3),
            ("python3 verify.py tampered.json", 7),
        ],
        caption="Change a single judge's score and the verifier refuses the bundle.",
    )

    # ---------------------------------------------------------------- outro
    d.card("Recap", "Create, submit, judge, publish, verify",
           "One container, offline, seeded from the shared fixtures. Roles enforced in "
           "the API. A winner that anyone can prove from the signed bundle.<br><br>"
           "<span style='font-size:22px;color:#7fc79a'>docker compose up &nbsp;·&nbsp; "
           "python3 verify.py bundle.json</span>", RECAP_SECS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=os.path.join(REPO, ".demo-work"))
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--out", default=os.path.join(REPO, "demo", "dogfood-demo.mp4"))
    args = ap.parse_args()

    workdir = os.path.abspath(args.workdir)
    os.makedirs(workdir, exist_ok=True)
    base = f"http://localhost:{args.port}"

    # A copy of .dogfood.toml pointed at this port, for the acceptance checker scene.
    with open(os.path.join(REPO, ".dogfood.toml")) as fh:
        toml = fh.read().replace("http://localhost:8080", base)
    with open(os.path.join(workdir, "demo.dogfood.toml"), "w") as fh:
        fh.write(toml)
    # The checker reads fixtures.json beside run.py, and verify.py imports the core,
    # so the terminal runs from a mirror of the repo root.
    for name in ("spec", "verify.py", "src"):
        link = os.path.join(workdir, name)
        if not os.path.lexists(link):
            os.symlink(os.path.join(REPO, name), link)

    shell_env = dict(os.environ)
    shell_env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + shell_env["PATH"]
    shell_env.update(TOKENS)

    proc, seed_output = boot_portal(workdir, args.port)
    raw_dir = os.path.join(workdir, "raw")
    shutil.rmtree(raw_dir, ignore_errors=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": W, "height": H},
                                      record_video_dir=raw_dir,
                                      record_video_size={"width": W, "height": H})
            ctx.add_init_script(OVERLAY_JS)
            page = ctx.new_page()
            page.set_default_timeout(15000)
            demo = Demo(page, base, shell_env, workdir)
            script(demo, seed_output)
            video_path = page.video.path()
            ctx.close()
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", video_path, "-r", "30",
         "-c:v", "libx264", "-preset", "slow", "-crf", "26", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", args.out],
        check=True,
    )
    print("wrote", args.out)


if __name__ == "__main__":
    main()
