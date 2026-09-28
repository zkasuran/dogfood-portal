#!/usr/bin/env python3
"""Record and produce the DOGFOOD portal demo video, end to end, against a live portal.

Every screen is real: the portal is booted fresh from spec/fixtures.json, the
browser scenes drive the actual UI, and the terminal scenes run the exact commands
they show and replay their real output. Nothing is mocked.

Two phases:

  1. capture   A retina (3200x1800) CDP screencast of a headless Chromium session,
               plus an event log: narration lines, cursor moves, clicks, zoom
               targets, scene cuts. Narration is synthesized first (Kokoro TTS),
               so every beat on screen is held exactly as long as its voice line.
  2. produce   demo/postprod.py composites each frame at 60 fps in the style of
               Screen Studio (window on a wallpaper, rendered cursor with click
               ripples, spring-animated zoom and pan), mixes voice over original
               music (demo/music.py) and writes the MP4, SRT subtitles and a
               YouTube chapter list.

    pip install -r requirements.txt playwright imageio-ffmpeg kokoro-onnx soundfile numpy pillow
    playwright install chromium
    python3 demo/record_demo.py --models /path/to/kokoro      # see demo/voice.py

Options: --workdir, --port, --out, --height (2160 or 1440), --skip-capture.
"""
import argparse
import base64
import contextlib
import html
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "src")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

VW, VH, DSF = 1600, 900, 2  # CSS viewport and device scale: frames are 3200x1800

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
    for cmd in (["migrate", "--noinput"], ["collectstatic", "--noinput"]):
        subprocess.run([py, "manage.py", *cmd], cwd=SRC, env=env, check=True,
                       capture_output=True)
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


# ------------------------------------------------------------------ page designs

FONT = "'Inter','Noto Sans',system-ui,sans-serif"
MONO = "'JetBrains Mono','DejaVu Sans Mono','Noto Sans Mono',monospace"
FONT_FACES = ""  # filled by load_fonts(): Inter and JetBrains Mono embedded as data URLs

FONT_FILES = [("Inter", 400, "Inter-Regular.ttf"), ("Inter", 500, "Inter-Medium.ttf"),
              ("Inter", 600, "Inter-SemiBold.ttf"), ("Inter", 700, "Inter-Bold.ttf"),
              ("Inter", 800, "Inter-ExtraBold.ttf"),
              ("JetBrains Mono", 400, "JetBrainsMono-Regular.ttf"),
              ("JetBrains Mono", 700, "JetBrainsMono-Bold.ttf")]


def load_fonts(font_dir):
    """Embed the title and terminal fonts (both SIL OFL) if they are present, so the
    look does not depend on what the host has installed."""
    global FONT_FACES
    faces = []
    for family, weight, name in FONT_FILES:
        path = os.path.join(font_dir, name)
        if os.path.exists(path):
            with open(path, "rb") as fh:
                data = base64.b64encode(fh.read()).decode()
            faces.append(f"@font-face{{font-family:'{family}';font-weight:{weight};"
                         f"src:url(data:font/ttf;base64,{data}) format('truetype')}}")
    FONT_FACES = "".join(faces)
    return len(faces)

CARD_CSS = f"""
html,body{{margin:0;height:100%;overflow:hidden;font-family:{FONT};color:#eef5f0;
  background:#07110c}}
.bg{{position:fixed;inset:-20%;background:
  radial-gradient(40% 50% at 20% 30%,rgba(47,140,94,.55),transparent 70%),
  radial-gradient(35% 45% at 80% 70%,rgba(24,98,120,.45),transparent 70%),
  radial-gradient(30% 40% at 65% 20%,rgba(160,200,90,.18),transparent 70%);
  filter:blur(20px);animation:drift 14s ease-in-out infinite alternate}}
@keyframes drift{{from{{transform:translate(0,0) scale(1)}}to{{transform:translate(-4%,3%) scale(1.08)}}}}
.wrap{{position:relative;height:100%;display:flex;flex-direction:column;
  justify-content:center;padding:0 150px;box-sizing:border-box}}
.in{{opacity:0;transform:translateY(26px);animation:in .9s cubic-bezier(.2,.8,.2,1) forwards}}
@keyframes in{{to{{opacity:1;transform:none}}}}
.kicker{{font-size:22px;font-weight:600;letter-spacing:6px;text-transform:uppercase;
  color:#8fe0ae}}
h1{{font-size:104px;line-height:1.02;margin:22px 0 26px;font-weight:800;letter-spacing:-2px;
  background:linear-gradient(90deg,#ffffff,#b8f0cc 60%,#7fd6c0);-webkit-background-clip:text;
  color:transparent}}
.sub{{font-size:32px;line-height:1.5;color:#c3d6ca;max-width:1150px}}
.sub b{{color:#fff}}
.num{{font-size:260px;font-weight:800;line-height:.8;color:rgba(143,224,174,.13);
  position:absolute;right:140px;bottom:120px;letter-spacing:-8px}}
.steps{{display:flex;gap:16px;margin-top:54px}}
.steps span{{font-size:22px;font-weight:600;padding:12px 26px;border-radius:40px;
  background:rgba(255,255,255,.06);color:#93ad9d;border:1px solid rgba(255,255,255,.08)}}
.steps span.on{{background:#2f8c5e;color:#fff;border-color:#46b37c;
  box-shadow:0 0 32px rgba(70,179,124,.55)}}
.steps span.done{{color:#cfe9da;background:rgba(47,140,94,.25)}}
.cmd{{margin-top:44px;font:26px {MONO};color:#8fe0ae;font-variant-ligatures:none}}
"""

TERM_CSS = f"""
html,body{{margin:0;height:100%;background:#0b0f14;overflow:hidden}}
.term{{height:100%;box-sizing:border-box;padding:30px 40px 40px;color:#d5dee6;
  font:19.5px/1.55 {MONO};overflow:hidden;font-variant-ligatures:none;
  font-feature-settings:"liga" 0,"calt" 0}}
pre{{margin:0;white-space:pre-wrap;word-break:break-all;font:inherit}}
.p{{color:#58d68d;font-weight:bold}}.path{{color:#6cb6ff}}.c{{color:#fff}}
.ok{{color:#3fdc7a;font-weight:bold}}.bad{{color:#ff6b6b;font-weight:bold}}
.caret{{display:inline-block;width:11px;height:22px;background:#d5dee6;
  vertical-align:-4px;animation:blink 1s steps(1) infinite}}
@keyframes blink{{50%{{opacity:0}}}}
"""

STEP_NAMES = ["Create", "Submit", "Judge", "Publish", "Verify"]


def card_html(kicker, title, sub, step=None, number=None, extra=""):
    steps = ""
    if step is not None:
        steps = '<div class="steps in" style="animation-delay:.45s">' + "".join(
            f'<span class="{"on" if i == step else "done" if i < step else ""}">{n}</span>'
            for i, n in enumerate(STEP_NAMES)
        ) + "</div>"
    num = f'<div class="num in" style="animation-delay:.2s">{number}</div>' if number else ""
    return (
        f"<style>{FONT_FACES}{CARD_CSS}</style><div class='bg'></div>"
        f"<div class='wrap'>{num}<div class='kicker in'>{kicker}</div>"
        f"<h1 class='in' style='animation-delay:.12s'>{title}</h1>"
        f"<div class='sub in' style='animation-delay:.28s'>{sub}</div>{steps}{extra}</div>"
    )


TERM_TYPE_JS = """([cmd, secs]) => new Promise(done => {
  const t = document.getElementById('t');
  document.querySelectorAll('.caret').forEach(c => c.remove());
  const line = document.createElement('div');
  line.innerHTML = '<span class="path">~/dogfood-portal</span> <span class="p">\u276f</span> ';
  const c = document.createElement('span'); c.className = 'c';
  const caret = document.createElement('span'); caret.className = 'caret';
  line.appendChild(c); line.appendChild(caret); t.appendChild(line);
  const per = Math.min(38, secs * 1000 / Math.max(1, cmd.length));
  let i = 0;
  const tick = () => { i += 1; c.textContent = cmd.slice(0, i); trim();
    if (i < cmd.length) setTimeout(tick, per * (0.6 + Math.random() * 0.8)); else done(); };
  const trim = () => { const box = t.parentElement;
    while (box.scrollHeight > box.clientHeight && t.firstChild) t.removeChild(t.firstChild); };
  tick();
})"""

TERM_OUT_JS = """([out]) => new Promise(done => {
  const t = document.getElementById('t');
  document.querySelectorAll('.caret').forEach(c => c.remove());
  const lines = out ? out.split('\\n') : [];
  const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;');
  const paint = s => {
    const e = esc(s);
    if (e.includes('NOT VERIFIED'))
      return e.replace('NOT VERIFIED', '<span class="bad">NOT VERIFIED</span>');
    return e
      .replace(/\\b(FAIL|HTTP\\/1\\.1 4\\d\\d.*|HTTP 40\\d)/g, '<span class="bad">$1</span>')
      .replace(/\\b(PASS|VERIFIED|HTTP\\/1\\.1 20\\d.*)/g, '<span class="ok">$1</span>');
  };
  let i = 0;
  const trim = () => { const box = t.parentElement;
    while (box.scrollHeight > box.clientHeight && t.firstChild) t.removeChild(t.firstChild); };
  const step = () => {
    if (i >= lines.length) {
      const idle = document.createElement('div');
      idle.innerHTML = '<span class="path">~/dogfood-portal</span> <span class="p">\u276f</span> <span class="caret"></span>';
      t.appendChild(idle); trim(); done(); return; }
    const s = document.createElement('div');
    s.className = 'out'; s.innerHTML = paint(lines[i++]) || '&nbsp;';
    t.appendChild(s); trim();
    setTimeout(step, 24);
  };
  step();
})"""

# Bounding box (CSS px) of the last `n` output lines of the terminal, for zooming.
TERM_BBOX_JS = """([n, skip]) => {
  const outs = [...document.querySelectorAll('#t .out')];
  const sel = outs.slice(Math.max(0, outs.length - n - skip), outs.length - skip);
  if (!sel.length) return null;
  const r = sel.map(e => e.getBoundingClientRect());
  const text = sel.map(e => e.textContent.length);
  const x0 = Math.min(...r.map(b => b.left)), y0 = Math.min(...r.map(b => b.top));
  const y1 = Math.max(...r.map(b => b.bottom));
  const w = Math.max(...text) * 11.8;
  return [x0, y0, Math.min(1560, x0 + w), y1];
}"""


# ----------------------------------------------------------------------- recorder


class Recorder:
    """Owns the screencast and the event log. Time is 'timeline' time: wall-clock
    seconds since start with paused spans (command execution, TTS, page loads)
    cut out, so the video never shows the machine thinking."""

    def __init__(self, ctx, page, out_dir):
        self.out_dir = out_dir
        self.frames_dir = os.path.join(out_dir, "frames")
        shutil.rmtree(out_dir, ignore_errors=True)
        os.makedirs(self.frames_dir)
        self.frames = []  # (wall_ts, filename)
        self.events = []
        self.pauses = []
        self.t0 = time.time()
        self._paused_at = None
        self.cdp = ctx.new_cdp_session(page)
        self.cdp.on("Page.screencastFrame", self._frame)
        self.cdp.send("Page.startScreencast", {
            "format": "jpeg", "quality": 94, "everyNthFrame": 1,
            "maxWidth": VW * DSF, "maxHeight": VH * DSF,
        })

    def _frame(self, ev):
        name = f"{len(self.frames):06d}.jpg"
        with open(os.path.join(self.frames_dir, name), "wb") as fh:
            fh.write(base64.b64decode(ev["data"]))
        self.frames.append((ev["metadata"]["timestamp"], name))
        try:
            self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
        except Exception:
            pass

    def now(self):
        wall = self._paused_at if self._paused_at is not None else time.time()
        return wall - self.t0 - sum(b - a for a, b in self.pauses)

    @contextlib.contextmanager
    def paused(self):
        if self._paused_at is not None:
            yield
            return
        self._paused_at = time.time()
        try:
            yield
        finally:
            self.pauses.append((self._paused_at, time.time()))
            self._paused_at = None

    def log(self, etype, **kw):
        kw.setdefault("t", round(self.now(), 4))
        self.events.append({"type": etype, **kw})

    def save(self):
        try:
            self.cdp.send("Page.stopScreencast")
        except Exception:
            pass
        end = self.now()
        with open(os.path.join(self.out_dir, "events.json"), "w") as fh:
            json.dump({"viewport": [VW, VH], "dsf": DSF, "t0": self.t0,
                       "pauses": self.pauses, "frames": self.frames,
                       "events": self.events, "end": end}, fh)
        return end


class Demo:
    def __init__(self, page, rec, voice, base, shell_env, workdir):
        self.page, self.rec, self.voice = page, rec, voice
        self.base, self.env, self.workdir = base, shell_env, workdir
        self.mouse = (VW * 0.62, VH * 0.58)
        self.kind = None

    # ------------------------------------------------------------ time & voice
    def hold(self, secs):
        if secs > 0:
            self.page.wait_for_timeout(int(secs * 1000))

    @contextlib.contextmanager
    def say(self, text, spoken=None, tail=0.45, lead=0.15):
        """Speak a narration line while the block runs; the beat lasts at least as
        long as the line (plus a breath)."""
        with self.rec.paused():
            path, dur = self.voice.clip(spoken or text)
        start = self.rec.now() + lead
        self.rec.log("say", t=round(start, 4), wav=path, text=text, dur=round(dur, 4))
        yield
        self.hold(start + dur + tail - self.rec.now())

    def chapter(self, name):
        self.rec.log("chapter", name=name)

    # ----------------------------------------------------------------- scenes
    def _scene(self, kind, **kw):
        self.kind = kind
        self.rec.log("scene", kind=kind, **kw)

    def card(self, html_doc, label=None):
        first = len(self.rec.frames)
        with self.rec.paused():
            self.page.set_content(html_doc)
            self.page.evaluate("document.fonts.ready")
            self.page.wait_for_timeout(50)
        self._scene("card", label=label, first=first)

    def goto(self, path):
        first = len(self.rec.frames)
        with self.rec.paused():
            self.page.goto(self.base + path)
            self.page.wait_for_load_state("networkidle")
        self._scene("browser", url=self.page.url, first=first)
        self.rec.log("zoom", s=1.0, snap=True)
        self.rec.log("move", x=self.mouse[0], y=self.mouse[1], dur=0)

    def _sync_url(self):
        self.rec.log("url", url=self.page.url)

    def terminal(self, title):
        first = len(self.rec.frames)
        with self.rec.paused():
            self.page.set_content(f"<style>{FONT_FACES}{TERM_CSS}</style><div class='term'><pre id='t'>"
                                  "</pre></div>")
            self.page.evaluate("document.fonts.ready")
            self.page.wait_for_timeout(50)
        self._scene("terminal", title=title, first=first)
        self.rec.log("zoom", s=1.0, snap=True)

    # ---------------------------------------------------------------- pointer
    def move_to(self, x, y, speed=1.0):
        fx, fy = self.mouse
        dist = ((x - fx) ** 2 + (y - fy) ** 2) ** 0.5
        dur = 0 if dist < 2 else min(1.1, 0.38 + dist / 1900) / speed
        self.rec.log("move", x=round(x, 1), y=round(y, 1), dur=round(dur, 3))
        self.page.mouse.move(x, y, steps=max(1, int(dur * 20)))
        self.mouse = (x, y)
        self.hold(dur)

    def _visible(self, loc):
        """Smooth-scroll the element into the middle band of the viewport."""
        box = loc.bounding_box()
        if box is None:
            loc.scroll_into_view_if_needed()
            return loc.bounding_box()
        if box["y"] < 70 or box["y"] + box["height"] > VH - 70:
            delta = box["y"] + box["height"] / 2 - VH * 0.55
            self.page.evaluate("d => window.scrollBy({top: d, behavior: 'smooth'})", delta)
            self.hold(0.9)
            box = loc.bounding_box()
        return box

    def point(self, selector, speed=1.0):
        loc = self.page.locator(selector).first
        box = self._visible(loc)
        x = box["x"] + min(box["width"] / 2, 60 if box["width"] > 200 else box["width"] / 2)
        y = box["y"] + box["height"] / 2
        self.move_to(x, y, speed)
        return loc, box

    def click(self, selector, pause=0.5, navigates=False):
        loc, _ = self.point(selector)
        self.hold(0.12)
        self.rec.log("click", x=self.mouse[0], y=self.mouse[1])
        if navigates:
            with self.rec.paused():
                loc.click()
                self.page.wait_for_load_state("networkidle")
            self._sync_url()
            self.rec.log("zoom", s=1.0)
        else:
            loc.click()
        self.hold(pause)

    def focus_zoom(self, selector, s=1.75, container=".form-row"):
        """Zoom the camera onto an element (its form row when inside the admin)."""
        box = self.page.evaluate(
            """([sel, cont]) => { const e = document.querySelector(sel); if (!e) return null;
                 const row = (cont && e.closest(cont)) || e; const r = row.getBoundingClientRect();
                 const f = e.getBoundingClientRect();
                 return [Math.min(r.left, f.left), r.top, Math.max(f.right, r.left + 700), r.bottom]; }""",
            [selector, container],
        )
        if box:
            self.zoom_box(box, s)

    def zoom_box(self, box, s):
        x0, y0, x1, y1 = box
        self.rec.log("zoom", s=s, x=round((x0 + x1) / 2, 1), y=round((y0 + y1) / 2, 1),
                     box=[round(v, 1) for v in (x0, y0, x1, y1)])

    def unzoom(self):
        self.rec.log("zoom", s=1.0)

    def type_into(self, selector, text, delay=55, zoom=1.75):
        self.page.locator(selector).first.fill("")
        if zoom:
            self.focus_zoom(selector, zoom)
        self.click(selector, pause=0.15)
        self.page.keyboard.type(text, delay=delay)
        self.hold(0.2)

    def select(self, selector, label, zoom=1.75):
        if zoom:
            self.focus_zoom(selector, zoom)
        loc, _ = self.point(selector)
        self.hold(0.1)
        self.rec.log("click", x=self.mouse[0], y=self.mouse[1])
        self.hold(0.25)
        self.page.select_option(selector, label=label)
        self.hold(0.45)

    # --------------------------------------------------------------- terminal
    def run(self, cmd):
        out = subprocess.run(["sh", "-c", cmd], cwd=self.workdir, env=self.env,
                             capture_output=True, text=True)
        return (out.stdout + out.stderr).rstrip("\n")

    def cmd(self, command, type_secs=1.8, after=0.3):
        with self.rec.paused():
            output = self.run(command)
        self.page.evaluate(TERM_TYPE_JS, [command, type_secs])
        self.hold(0.35)
        self.page.evaluate(TERM_OUT_JS, [output])
        self.hold(after)
        return output

    def term_zoom(self, lines, s=1.6, skip=0):
        box = self.page.evaluate(TERM_BBOX_JS, [lines, skip])
        if box:
            self.zoom_box(box, s)


# -------------------------------------------------------------------------- script

PY_COUNT = ("python3 -c 'import json,sys; print(len(json.load(sys.stdin)), "
            "\"projects loaded from spec/fixtures.json\")'")


def script(d, seed_output):
    B = d.base

    # ------------------------------------------------------------------ intro
    d.chapter("Intro")
    d.card(card_html("DOGFOOD 2026", "DOGFOOD portal",
                     "Self-hostable hackathon submission and judging, with "
                     "<b>reproducible, signed results</b>."), label="intro")
    d.hold(0.6)
    with d.say("This is the DOGFOOD portal: self-hostable hackathon submission and judging, "
               "with results that anyone can reproduce and verify, without trusting whoever "
               "runs the server.",
               spoken="This is the Dogfood portal: self-hostable hackathon submission and "
                      "judging, with results that anyone can reproduce, and verify, without "
                      "trusting whoever runs the server."):
        pass
    d.card(card_html("One full event lifecycle", "Five steps, one live portal",
                     "Create an event, submit a project, judge it, publish the results, and "
                     "verify them independently. <b>Nothing on screen is mocked.</b>",
                     step=-1), label="overview")
    with d.say("In the next few minutes, we'll run one full event lifecycle on a live portal. "
               "The organizer creates an event, a team submits, a judge scores, the organizer "
               "publishes, and a standalone verifier proves the result."):
        pass

    # ------------------------------------------------------------------ boot
    d.chapter("Boot: one container, offline")
    boot_lines = "\n".join(seed_output.strip().splitlines()[:9])
    with open(os.path.join(d.workdir, "seed-output.txt"), "w") as fh:
        fh.write(boot_lines + "\n")
    d.terminal("docker compose up  ·  network off")
    with d.say("It's one container, and it runs fully offline. On first boot, it migrates, "
               "loads the shared fixtures, and creates one seed account for each role."):
        d.cmd("cat seed-output.txt   # first-boot output of docker compose up", 1.6)
        d.term_zoom(9, 1.45)
    d.unzoom()
    with d.say("The health check answers, and forty-one fixture projects are ready."):
        d.cmd(f"curl -s {B}/health", 0.9)
        d.cmd(f"curl -s {B}/api/projects | {PY_COUNT}", 1.4)
        d.term_zoom(3, 1.7, skip=0)
    d.hold(0.4)

    # ---------------------------------------------------------------- CREATE
    d.chapter("Step 1: Create")
    d.card(card_html("Step 1", "Create", "The organizer sets up an event, a team and the "
                     "weighted rubric.", step=0, number="01"), label="Create")
    with d.say("Step one: create."):
        pass
    d.hold(0.8)
    d.hold(0.5)
    d.goto("/")
    with d.say("Here's the public home page. The organizer console is one click away."):
        d.hold(0.6)
        d.point("text=Organizer console")
    d.click("text=Organizer console", navigates=True)
    with d.say("The organizer signs in with the seed login."):
        d.type_into("#id_username", "organizer", delay=80, zoom=1.6)
        d.type_into("#id_password", "organizer", delay=80, zoom=1.6)
        d.click("input[type=submit]", navigates=True)
    with d.say("Every core table is browsable from the console."):
        d.hold(0.4)
        d.point("text=Events")
        d.hold(0.6)

    d.goto("/admin/core/event/add/")
    with d.say("We create a new event, DOGFOOD Demo Finals, with its submission deadline at "
               "the end of December.",
               spoken="We create a new event, Dogfood Demo Finals, with its submission "
                      "deadline at the end of December."):
        d.type_into("#id_external_id", "evt_demo")
        d.type_into("#id_name", "DOGFOOD Demo Finals")
        d.type_into("#id_submissions_close_0", "2026-12-31", delay=45)
        d.type_into("#id_submissions_close_1", "18:00:00", delay=45)
    d.type_into("#id_starts_at_0", "2026-12-01", delay=35)
    d.type_into("#id_starts_at_1", "18:00:00", delay=35)
    d.type_into("#id_ends_at_0", "2026-12-31", delay=35)
    d.type_into("#id_ends_at_1", "18:00:00", delay=35)
    d.unzoom()
    d.click("input[name=_save]", navigates=True)
    with d.say("Saved. It sits right alongside the fixture event, Sample Hack twenty "
               "twenty-six.", spoken="Saved. It sits right alongside the fixture event, "
               "Sample Hack, twenty twenty-six."):
        d.hold(0.3)
        d.point("#result_list tbody tr:first-child a")
        d.hold(0.5)

    d.goto("/admin/core/team/add/")
    with d.say("Next, a team for the new event: Demo Crew."):
        d.type_into("#id_external_id", "team_demo")
        d.select("#id_event", "DOGFOOD Demo Finals")
        d.type_into("#id_name", "Demo Crew")
    d.unzoom()
    d.click("input[name=_save]", navigates=True)
    d.hold(0.8)

    d.goto("/admin/core/rubric/1/change/")
    with d.say("And the rubric. Each criterion carries a weight the organizer controls: "
               "forty percent functionality, forty percent quality, and twenty percent "
               "innovation."):
        d.hold(0.8)
        d.focus_zoom("#id_criteria-0-weight, input[name$='weight']", 1.45,
                     container="fieldset, .inline-group")
        d.point("input[name$='weight']")
        d.hold(0.5)
    with d.say("These weights ship inside the signed results, so anyone can see exactly how "
               "the scores were combined."):
        pass
    d.unzoom()

    # ---------------------------------------------------------------- SUBMIT
    d.chapter("Step 2: Submit")
    d.card(card_html("Step 2", "Submit", "Projects land in a public gallery. Deadlines are "
                     "enforced by the backend, not by hiding a button.", step=1, number="02"),
           label="Submit")
    with d.say("Step two: submit."):
        pass
    d.hold(0.8)
    d.hold(0.4)
    d.goto("/projects")
    with d.say("Projects land in a public gallery. No login needed."):
        d.hold(0.5)
        d.zoom_box([550, 60, 1300, 300], 1.5)
        d.point("input[name=q]")
    with d.say("Search covers titles and summaries."):
        d.type_into("input[name=q]", "Signal", delay=110, zoom=0)
        d.click("button[type=submit]", navigates=True)
        d.zoom_box([550, 60, 1300, 560], 1.35)
        d.hold(0.6)
    d.unzoom()

    d.terminal("the deadline is enforced in the API")
    with d.say("Deadlines are enforced by the back end, not by hiding a button. Sample Hack "
               "closed in March, so a participant posting a late entry gets a four-oh-three."):
        d.cmd(f"curl -si -X POST {B}/api/projects -H \"Authorization: Token $PARTICIPANT\" "
              "-H 'Content-Type: application/json' -d '{\"title\":\"Late entry\"}' "
              "| sed -n '1p;$p'", 2.2)
        d.term_zoom(2, 1.7)
    d.hold(0.3)
    d.goto("/admin/core/auditlog/")
    with d.say("And the refusal is written to the audit log."):
        d.zoom_box([0, 150, 1600, 330], 1.5)
        d.point("#result_list tbody tr:first-child td:nth-child(3)")
        d.hold(0.4)
    d.unzoom()

    d.goto("/admin/core/project/add/")
    with d.say("In this build, a new entry is recorded through the organizer console. Demo "
               "Lantern goes in for Demo Crew, on the developer tools track."):
        d.type_into("#id_external_id", "prj_demo")
        d.select("#id_team", "Demo Crew")
        d.select("#id_track", "Developer tools")
        d.type_into("#id_title", "Demo Lantern")
    d.type_into("#id_summary", "Lights up the judging trail from raw score to winner.", delay=22)
    d.type_into("#id_repo_url", "https://example.org/repo/demo-lantern", delay=22)
    with d.say("And it's marked as submitted."):
        d.select("#id_status", "Submitted")
        d.type_into("#id_submitted_at_0", "2026-12-10", delay=35)
        d.type_into("#id_submitted_at_1", "12:00:00", delay=35)
    d.unzoom()
    d.click("input[name=_save]", navigates=True)
    d.hold(0.6)
    d.goto("/projects?q=Lantern")
    with d.say("And there it is, live in the public gallery."):
        d.zoom_box([550, 60, 1300, 560], 1.45)
        d.point("text=Demo Lantern")
        d.hold(0.4)
    d.unzoom()

    # ---------------------------------------------------------------- JUDGE
    d.chapter("Step 3: Judge")
    d.card(card_html("Step 3", "Judge", "A judge scores the project on the weighted rubric. "
                     "Judges see their own scores and nobody else's.", step=2, number="03"),
           label="Judge")
    with d.say("Step three: judge."):
        pass
    d.hold(0.8)
    d.hold(0.4)
    d.goto("/admin/core/score/add/")
    with d.say("Judge oh-seven scores Demo Lantern on the weighted rubric: fives for "
               "functionality and quality, and a four for innovation."):
        d.select("#id_judge", "jdg_07 (judge)")
        d.select("#id_project", "Demo Lantern")
        d.type_into("#id_comment", "Clear trail from raw score to rank. Solid build.", delay=24)
        for i, (label, value) in enumerate(
            [("Functionality (w=0.400)", "5"), ("Quality (w=0.400)", "5"),
             ("Innovation (w=0.200)", "4")]
        ):
            d.click("text=Add another Score value", pause=0.3)
            d.select(f"#id_values-{i}-criterion", label, zoom=1.6)
            d.type_into(f"#id_values-{i}-value", value, delay=90, zoom=0)
    d.unzoom()
    d.click("input[name=_save]", navigates=True)
    with d.say("Score saved."):
        pass

    d.terminal("role isolation lives in the backend")
    with d.say("Role isolation lives in the API. Judge oh-seven sees only their own scores, "
               "including Demo Lantern."):
        d.cmd(f"curl -s {B}/api/judge/scores -H \"Authorization: Token $JUDGE_A\" "
              "| python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[\"judge_id\"], "
              "d[\"count\"], \"scores\"); [print(\"  \", s[\"project\"], s[\"project_title\"], "
              "s[\"criteria\"]) for s in d[\"scores\"]]'", 2.0)
        d.term_zoom(5, 1.5)
    d.unzoom()
    with d.say("When another judge asks for those scores, the API refuses with a "
               "four-oh-three. So does a participant."):
        d.cmd(f"curl -s -w '  -> HTTP %{{http_code}}\\n' '{B}/api/judge/scores?judge_id=jdg_07' "
              "-H \"Authorization: Token $JUDGE_B\"", 1.6)
        d.cmd(f"curl -s -w '  -> HTTP %{{http_code}}\\n' {B}/api/judge/scores "
              "-H \"Authorization: Token $PARTICIPANT\"", 1.3)
        d.term_zoom(4, 1.5)
    d.unzoom()
    with d.say("The organizer, meanwhile, sees judging progress across the whole event."):
        d.cmd(f"curl -s {B}/api/progress -H \"Authorization: Token $ORG\"", 1.2)
    d.hold(0.5)

    d.terminal("the official DOGFOOD acceptance checker")
    with d.say("Now the unmodified DOGFOOD acceptance checker, run against this live portal.",
               spoken="Now, the unmodified Dogfood acceptance checker, run against this live "
                      "portal."):
        d.cmd("python3 spec/run.py demo.dogfood.toml", 1.2)
    with d.say("Seven of seven checks pass."):
        d.term_zoom(4, 1.55, skip=1)
    d.hold(0.3)
    d.unzoom()

    # ---------------------------------------------------------------- PUBLISH
    d.chapter("Step 4: Publish")
    d.card(card_html("Step 4", "Publish", "Results stay hidden until the organizer publishes. "
                     "Publishing and signing are one action.", step=3, number="04"),
           label="Publish")
    with d.say("Step four: publish."):
        pass
    d.hold(0.8)
    d.hold(0.4)
    d.terminal("results are hidden, and only an organizer can publish")
    with d.say("Before publishing, the public sees nothing. There's no results bundle yet, and "
               "a judge who tries to publish is refused."):
        d.cmd(f"curl -s {B}/api/results", 0.9)
        d.cmd(f"curl -s -w '  -> HTTP %{{http_code}}\\n' {B}/api/results/bundle", 1.0)
        d.cmd(f"curl -s -w '  -> HTTP %{{http_code}}\\n' -X POST {B}/api/results/publish "
              "-H \"Authorization: Token $JUDGE_A\"", 1.3)
    with d.say("Only the organizer can publish, and publishing and signing are a single "
               "action. Back comes a signed bundle, with its digest, the Ed25519 signature, "
               "and the exact code commit.",
               spoken="Only the organizer can publish, and publishing and signing are a single "
                      "action. Back comes a signed bundle, with its digest, the E D "
                      "twenty-five five-nineteen signature, and the exact code commit."):
        d.cmd(f"curl -s -X POST {B}/api/results/publish -H \"Authorization: Token $ORG\" "
              "| python3 -c 'import json,sys; b=json.load(sys.stdin); p=b[\"payload\"]; "
              "print(\"published, method:\", p[\"method\"]); print(\"digest:   \", b[\"digest\"]); "
              "print(\"signature:\", b[\"signature\"][:64] + \"...\"); "
              "print(\"commit:   \", p.get(\"code_commit\"))'", 2.0)
        d.term_zoom(4, 1.5)
    d.unzoom()

    d.terminal("the published ranking, now public")
    with d.say("The ranking is now public, with scores adjusted for harsh and generous judges, "
               "as documented in JUDGING.md.",
               spoken="The ranking is now public, with scores adjusted for harsh and generous "
                      "judges, as documented in judging dot M D."):
        d.cmd(f"curl -s {B}/api/results | python3 -c 'import json,sys; r=json.load(sys.stdin); "
              "print(\"published:\", r[\"published\"], \"| method:\", r[\"method\"]); "
              "[print(\"  #%-2d %-9s final=%.3f n=%d\" % (x[\"rank\"], x[\"project\"], x[\"final\"], "
              "x[\"n\"])) for x in r[\"ranking\"][:8]]'", 2.0)
        d.term_zoom(9, 1.35)
    d.unzoom()
    with d.say("The organizer can also export the results as CSV."):
        d.cmd(f"curl -s {B}/api/export/results.csv -H \"Authorization: Token $ORG\" | head -n 5",
              1.3)
    d.hold(0.4)

    # ---------------------------------------------------------------- VERIFY
    d.chapter("Step 5: Verify")
    d.card(card_html("Step 5", "Verify", "Anyone downloads the bundle and checks it with the "
                     "standalone <b>verify.py</b>. No account, no trust in the host.",
                     step=4, number="05"), label="Verify")
    with d.say("Step five: verify."):
        pass
    d.hold(0.8)
    d.hold(0.4)
    d.terminal("verify the real bundle")
    with d.say("Anyone can download the bundle. It carries every raw score, the rubric "
               "weights, and the method, along with the portal's public verification key."):
        d.cmd(f"curl -s {B}/api/results/bundle -o bundle.json && python3 -c 'import json; "
              "p=json.load(open(\"bundle.json\"))[\"payload\"]; print(len(p[\"raw_scores\"]), "
              "\"raw scores |\", \"weights:\", p[\"rubric\"][\"weights\"], \"| method:\", "
              "p[\"method\"])'", 2.0)
        d.cmd(f"curl -s {B}/api/verification-key", 1.0)
    with d.say("The standalone verifier recomputes the ranking from the raw scores, checks the "
               "digest, and verifies the signature."):
        d.cmd("python3 verify.py bundle.json", 1.0)
        d.hold(0.3)
        d.term_zoom(5, 1.45, skip=1)
    with d.say("Verified."):
        pass
    d.unzoom()

    d.terminal("now tamper with a single score")
    with d.say("Now, let's tamper with a single judge's score, and bump it up to all fives."):
        d.cmd("python3 -c 'import json; b=json.load(open(\"bundle.json\")); "
              "s=next(r for r in b[\"payload\"][\"raw_scores\"] if r[\"project\"]!=\"prj_demo\" "
              "and min(r[\"criteria\"].values())<5); print(\"changing\", s[\"judge\"], \"on\", "
              "s[\"project\"], s[\"criteria\"], \"-> all 5s\"); "
              "s[\"criteria\"]={k:5 for k in s[\"criteria\"]}; "
              "json.dump(b, open(\"tampered.json\",\"w\"))'", 2.0)
    with d.say("Run the verifier again, and it refuses the bundle."):
        d.cmd("python3 verify.py tampered.json", 1.0)
        d.hold(0.2)
        d.term_zoom(5, 1.45, skip=1)
    with d.say("Not verified."):
        pass
    d.hold(0.4)
    d.unzoom()

    # ---------------------------------------------------------------- outro
    d.chapter("Recap")
    d.card(card_html("Recap", "Create, submit, judge,<br>publish, verify",
                     "One container, offline, seeded from the shared fixtures. Roles enforced "
                     "in the API. <b>A winner anyone can prove</b> from the signed bundle.",
                     step=5,
                     extra="<div class='cmd in' style='animation-delay:.6s'>docker compose up"
                           "&nbsp;&nbsp;·&nbsp;&nbsp;python3 verify.py bundle.json</div>"),
           label="Recap")
    with d.say("Create, submit, judge, publish, verify. One container, offline, seeded from "
               "the shared fixtures. Roles enforced in the API, and a winner that anyone can "
               "prove from the signed bundle. Thanks for watching."):
        pass
    d.hold(3.5)


# --------------------------------------------------------------------------- main


def capture(args, workdir, voice):
    from playwright.sync_api import sync_playwright

    base = f"http://localhost:{args.port}"
    with open(os.path.join(REPO, ".dogfood.toml")) as fh:
        toml = fh.read().replace("http://localhost:8080", base)
    with open(os.path.join(workdir, "demo.dogfood.toml"), "w") as fh:
        fh.write(toml)
    # The checker reads fixtures.json beside run.py and verify.py imports the core, so
    # the terminal runs from a mirror of the repo root.
    for name in ("spec", "verify.py", "src"):
        link = os.path.join(workdir, name)
        if not os.path.lexists(link):
            os.symlink(os.path.join(REPO, name), link)

    shell_env = dict(os.environ)
    shell_env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + shell_env["PATH"]
    shell_env.update(TOKENS)

    proc, seed_output = boot_portal(workdir, args.port)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=[f"--force-device-scale-factor={DSF}",
                                              "--font-render-hinting=none"])
            ctx = browser.new_context(viewport={"width": VW, "height": VH},
                                      device_scale_factor=DSF)
            page = ctx.new_page()
            page.set_default_timeout(15000)
            page.set_content("<body style='background:#07110c'></body>")
            rec = Recorder(ctx, page, os.path.join(workdir, "capture"))
            demo = Demo(page, rec, voice, base, shell_env, workdir)
            script(demo, seed_output)
            end = rec.save()
            ctx.close()
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    print(f"captured {end:.1f}s timeline")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=os.path.join(REPO, ".demo-work"))
    ap.add_argument("--models", default=os.path.join(REPO, ".demo-work", "kokoro"),
                    help="directory holding kokoro-v1.0.onnx and voices-v1.0.bin")
    ap.add_argument("--fonts", default=os.path.join(REPO, ".demo-work", "fonts"),
                    help="directory holding Inter-*.ttf and JetBrainsMono-*.ttf (optional)")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--out", default=os.path.join(REPO, "demo", "dogfood-demo.mp4"))
    ap.add_argument("--height", type=int, default=2160, choices=[1080, 1440, 2160])
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--skip-capture", action="store_true",
                    help="re-produce from the last capture in --workdir")
    ap.add_argument("--capture-only", action="store_true")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    args = ap.parse_args()

    workdir = os.path.abspath(args.workdir)
    os.makedirs(workdir, exist_ok=True)

    from voice import Voice

    voice = Voice(args.models, os.path.join(workdir, "voice-cache"))
    if not args.skip_capture:
        print("embedded fonts:", load_fonts(args.fonts))
        capture(args, workdir, voice)
    if args.capture_only:
        return

    import postprod

    postprod.FONT_DIRS.insert(0, args.fonts)

    postprod.produce(os.path.join(workdir, "capture"), args.out, height=args.height,
                     fps=args.fps, jobs=args.jobs)


if __name__ == "__main__":
    main()
