"""Post-production for the demo video: turns a capture (retina screencast frames plus
an event log, see record_demo.py) into a finished, YouTube-ready video.

Per output frame (60 fps) it composites, Screen Studio style:
  * a soft gradient wallpaper with the app framed as a rounded window with a shadow,
    a browser or terminal title bar drawn on top,
  * a spring-animated camera that zooms and pans onto whatever is being worked on,
  * a rendered macOS-style cursor that glides along eased curves, dips on click and
    throws a ripple,
  * crossfades between scenes and a chapter badge.

Audio: the narration clips at their logged times over the original music bed
(music.py), ducked under the voice, loudness-normalized to -14 LUFS / -1 dBTP.
Also writes <out>.srt (subtitles) and <out>.chapters.txt (YouTube chapter list).
"""
import bisect
import json
import math
import os
import re
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SRC_W, SRC_H = 3200, 1800  # capture frame size
BAR_SRC = 84               # title bar height, in source pixels
XFADE = 0.45               # scene crossfade, seconds
CURSOR_CSS = 26            # cursor height in CSS px
FONT_DIRS = [os.path.join(os.path.dirname(HERE), ".demo-work", "fonts"),
             "/usr/share/fonts/google-noto"]


def ffmpeg_exe():
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def font(size, weight="Medium"):
    for d in FONT_DIRS:
        for name in (f"Inter-{weight}.ttf", f"NotoSans-{weight}.ttf", "NotoSans-Regular.ttf"):
            p = os.path.join(d, name)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def ease_io(u):
    u = min(1.0, max(0.0, u))
    return 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


def ease_out(u):
    u = min(1.0, max(0.0, u))
    return 1 - (1 - u) ** 3


# ------------------------------------------------------------------------ timeline


class Timeline:
    def __init__(self, cap_dir, W, H, fps):
        with open(os.path.join(cap_dir, "events.json")) as fh:
            meta = json.load(fh)
        self.cap_dir = cap_dir
        self.W, self.H, self.fps = W, H, fps
        self.vw, self.vh = meta["viewport"]
        self.end = meta["end"]
        self.n = int(math.ceil(self.end * fps))
        t0, pauses = meta["t0"], meta["pauses"]

        # screencast frames on the timeline (pauses cut out)
        starts = [a for a, _ in pauses]
        cum = np.cumsum([0.0] + [b - a for a, b in pauses])

        def tl(ts):
            i = bisect.bisect_right(starts, ts) - 1  # last pause that began before ts
            if i < 0:
                return ts - t0
            a, b = pauses[i]
            if ts <= b:  # captured while paused: shown the moment the pause ends
                return a - t0 - cum[i]
            return ts - t0 - cum[i + 1]

        self.frame_t = [tl(ts) for ts, _ in meta["frames"]]
        self.frame_files = [os.path.join(cap_dir, "frames", f) for _, f in meta["frames"]]

        ev = sorted(meta["events"], key=lambda e: e["t"])
        self.says = [e for e in ev if e["type"] == "say"]
        self.chapters = [e for e in ev if e["type"] == "chapter"]
        self.scenes = []
        for e in ev:
            if e["type"] == "scene":
                self.scenes.append(dict(e, url=e.get("url"), first=e.get("first", 0)))
            elif e["type"] == "url" and self.scenes:
                self.scenes.append(dict(self.scenes[-1], t=e["t"], url=e["url"], cont=True))
        self.scene_t = [s["t"] for s in self.scenes]
        self.clicks = [e for e in ev if e["type"] == "click"]
        self.click_t = [c["t"] for c in self.clicks]
        self._layout()
        self._cursor(ev)
        self._camera(ev)

    # geometry --------------------------------------------------------------
    def _layout(self):
        W, H = self.W, self.H
        self.cw = round(W * 0.855)                  # window content width (stage px)
        self.k = self.cw / SRC_W                    # source px -> stage px
        self.ch = round(SRC_H * self.k)
        self.bar = round(BAR_SRC * self.k)
        total_h = self.ch + self.bar
        self.wx0 = (W - self.cw) / 2
        self.wy0 = (H - total_h) / 2
        self.cx0, self.cy0 = self.wx0, self.wy0 + self.bar
        self.win = (self.wx0, self.wy0, self.wx0 + self.cw, self.wy0 + total_h)
        self.radius = round(W * 0.0085)

    def css_to_stage(self, x, y):
        s = self.cw / self.vw
        return self.cx0 + x * s, self.cy0 + y * s

    def scene_at(self, t):
        i = bisect.bisect_right(self.scene_t, t) - 1
        return self.scenes[max(0, i)], max(0, i)

    def cut_before(self, t):
        """Time of the latest real scene change (not a URL update) at or before t."""
        i = bisect.bisect_right(self.scene_t, t) - 1
        while i > 0 and self.scenes[i].get("cont"):
            i -= 1
        return (self.scenes[i]["t"], i) if i >= 0 else (None, None)

    def frame_index(self, t):
        return max(0, bisect.bisect_right(self.frame_t, t + 1e-4) - 1)

    # cursor ----------------------------------------------------------------
    def _cursor(self, ev):
        self.moves = []
        self.move_t = []
        pos = (self.vw * 0.62, self.vh * 0.58)
        for e in ev:
            if e["type"] != "move":
                continue
            start = self.cursor_at(e["t"])[:2] if self.moves else pos
            self.moves.append((e["t"], e["dur"], start, (e["x"], e["y"])))
            self.move_t.append(e["t"])

    def cursor_at(self, t):
        i = bisect.bisect_right(self.move_t, t) - 1
        if i < 0:
            return (self.vw * 0.62, self.vh * 0.58, 0)
        t0, dur, (fx, fy), (tx, ty) = self.moves[i]
        u = 1.0 if dur <= 0 else ease_io((t - t0) / dur)
        x, y = fx + (tx - fx) * u, fy + (ty - fy) * u
        # slight arc, like a hand moving a mouse
        dx, dy = tx - fx, ty - fy
        arc = 0.08 * math.sin(math.pi * u)
        return (x - dy * arc, y + dx * arc, u)

    def press_at(self, t):
        i = bisect.bisect_right(self.click_t, t) - 1
        if i < 0:
            return 0.0, []
        age = t - self.click_t[i]
        press = 0.0
        if 0 <= age < 0.32:
            press = age / 0.07 if age < 0.07 else max(0.0, 1 - (age - 0.07) / 0.25)
        ripples = []
        j = i
        while j >= 0 and t - self.click_t[j] < 0.6:
            ripples.append((self.clicks[j]["x"], self.clicks[j]["y"], t - self.click_t[j]))
            j -= 1
        return press, ripples

    # camera ----------------------------------------------------------------
    def _target(self, e):
        W, H = self.W, self.H
        s = e.get("s", 1.0)
        if s <= 1.0 or "x" not in e:
            return (s, W / 2, H / 2)
        x, y = self.css_to_stage(e["x"], e["y"])
        if e.get("box"):  # never zoom so far that the thing we point at is cropped
            bx0, by0 = self.css_to_stage(*e["box"][:2])
            bx1, by1 = self.css_to_stage(*e["box"][2:])
            s = max(1.0, min(s, 0.9 * W / max(1, bx1 - bx0), 0.85 * H / max(1, by1 - by0)))
        hw, hh = W / (2 * s), H / (2 * s)
        m = W * 0.012
        x0, y0, x1, y1 = self.win
        lo, hi = x0 - m + hw, x1 + m - hw
        x = min(max(x, lo), hi) if lo <= hi else W / 2
        lo, hi = y0 - m + hh, y1 + m - hh
        y = min(max(y, lo), hi) if lo <= hi else H / 2
        return (s, x, y)

    def _camera(self, ev):
        """Critically damped spring toward the latest zoom target, simulated at the
        output frame rate so every worker sees the identical camera path."""
        zooms = [e for e in ev if e["type"] == "zoom"]
        zi = 0
        state = np.array([1.0, self.W / 2, self.H / 2])
        vel = np.zeros(3)
        target = state.copy()
        omega = 5.2
        dt = 1.0 / self.fps
        prev_kind = None
        self.cam = np.zeros((self.n + 1, 3))
        for f in range(self.n + 1):
            t = f * dt
            while zi < len(zooms) and zooms[zi]["t"] <= t:
                e = zooms[zi]
                target = np.array(self._target(e))
                if e.get("snap"):
                    scene, _ = self.scene_at(e["t"])
                    state = target.copy()
                    vel[:] = 0
                    if prev_kind == "card" and scene["kind"] != "card":
                        state[0] = 0.9  # the window rises in from the title card
                zi += 1
            scene, _ = self.scene_at(t)
            if scene["kind"] == "card":
                state = np.array([1.0, self.W / 2, self.H / 2])
                vel[:] = 0
                target = state.copy()
            prev_kind = scene["kind"]
            for _ in range(4):
                h = dt / 4
                acc = omega * omega * (target - state) - 2 * omega * vel
                vel += acc * h
                state += vel * h
            self.cam[f] = state


# ------------------------------------------------------------------------ artwork


def wallpaper(W, H, pad):
    """Deep green/teal gradient with soft light pools and fine grain."""
    w, h = W + 2 * pad, H + 2 * pad
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    xx /= w
    yy /= h
    c0 = np.array([6, 22, 17], np.float32)
    c1 = np.array([10, 46, 44], np.float32)
    g = (0.55 * xx + 0.45 * yy)[..., None]
    img = c0 * (1 - g) + c1 * g
    for cx, cy, r, col, a in [
        (0.18, 0.22, 0.45, (46, 150, 96), 0.55),
        (0.85, 0.80, 0.50, (22, 110, 128), 0.50),
        (0.70, 0.12, 0.30, (150, 200, 90), 0.16),
        (0.10, 0.95, 0.35, (20, 90, 70), 0.35),
    ]:
        d2 = ((xx - cx) * w / h) ** 2 + (yy - cy) ** 2
        img += np.array(col, np.float32) * a * np.exp(-d2 / (2 * (r / 2) ** 2))[..., None] * 0.6
    rng = np.random.default_rng(3)
    img += rng.normal(0, 1.6, (h, w, 1)).astype(np.float32)
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def backdrop(tl, pad):
    """Wallpaper plus the window's drop shadow, baked once at stage scale."""
    W, H = tl.W, tl.H
    img = wallpaper(W, H, pad).convert("RGBA")
    x0, y0, x1, y1 = tl.win
    sh = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(sh)
    off = W * 0.006
    d.rounded_rectangle([x0 + pad, y0 + pad + off, x1 + pad, y1 + pad + off * 2],
                        radius=tl.radius, fill=150)
    sh = sh.filter(ImageFilter.GaussianBlur(W * 0.014))
    black = Image.new("RGBA", img.size, (0, 0, 0, 255))
    black.putalpha(sh)
    img.alpha_composite(black)
    return img.convert("RGB")


def title_bar(kind, text, width):
    """Browser (light) or terminal (dark) window chrome, in source pixels."""
    h = BAR_SRC
    dark = kind == "terminal"
    bar = Image.new("RGB", (width, h), (28, 33, 40) if dark else (236, 238, 241))
    d = ImageDraw.Draw(bar)
    d.line([(0, h - 1), (width, h - 1)], fill=(18, 22, 27) if dark else (214, 217, 222), width=2)
    for i, col in enumerate([(255, 95, 87), (254, 188, 46), (40, 200, 64)]):
        cx, cy, r = 44 + i * 40, h // 2, 12
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    if dark:
        f = font(28, "Medium")
        label = f"\u2009{text}" if text else "Terminal"
        tw = d.textlength(label, font=f)
        d.text(((width - tw) / 2, h / 2), label, font=f, fill=(154, 164, 174), anchor="lm")
    else:
        pw, ph = int(width * 0.46), 52
        px, py = (width - pw) // 2, (h - ph) // 2
        d.rounded_rectangle([px, py, px + pw, py + ph], radius=ph // 2, fill=(255, 255, 255),
                            outline=(221, 224, 229), width=2)
        # padlock-free "info" dot then the url
        f = font(28, "Regular")
        url = re.sub(r"^https?://", "", text or "")
        host, _, path = url.partition("/")
        x = px + 34
        d.ellipse([x - 7, h / 2 - 7, x + 7, h / 2 + 7], outline=(120, 128, 138), width=3)
        x += 24
        d.text((x, h / 2), host, font=f, fill=(32, 36, 40), anchor="lm")
        x += d.textlength(host, font=f)
        d.text((x, h / 2), "/" + path if path or url.endswith("/") else "", font=f,
               fill=(120, 128, 138), anchor="lm")
        # nav arrows on the left of the pill
        f2 = font(34, "Regular")
        d.text((190, h / 2), "\u2039   \u203a", font=f2, fill=(150, 156, 164), anchor="lm")
    return bar


def cursor_sprite(height):
    """macOS-like arrow: white body, black outline, soft shadow. Hotspot at (pad, pad)."""
    S = 4
    pts = [(0, 0), (0, 16.5), (4.2, 12.6), (7.0, 19.0), (9.6, 17.9), (6.9, 11.7), (12.2, 11.7)]
    scale = height * S / 19.0
    pad = int(height * 0.35)
    size = (int(13 * scale / S) + 2 * pad, int(20 * scale / S) + 2 * pad)
    big = Image.new("RGBA", (size[0] * S, size[1] * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    o = pad * S
    poly = [(o + x * scale, o + y * scale) for x, y in pts]
    shadow = Image.new("L", big.size, 0)
    ImageDraw.Draw(shadow).polygon([(x + 0.8 * scale, y + 1.4 * scale) for x, y in poly], fill=110)
    shadow = shadow.filter(ImageFilter.GaussianBlur(1.3 * scale))
    sh = Image.new("RGBA", big.size, (0, 0, 0, 255))
    sh.putalpha(shadow)
    big.alpha_composite(sh)
    d.polygon(poly, fill=(0, 0, 0, 255))
    inner = [(o + x * scale, o + y * scale) for x, y in
             [(1.25, 2.9), (1.25, 13.7), (4.6, 10.6), (7.55, 17.2), (8.3, 16.9), (5.4, 10.4), (9.4, 10.4)]]
    d.polygon(inner, fill=(255, 255, 255, 255))
    return big.resize(size, Image.LANCZOS), pad


def chapter_badge(text, W):
    f = font(round(W * 0.0095), "SemiBold")
    h = round(W * 0.0175)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tw = tmp.textlength(text, font=f)
    w = int(tw + h * 1.55)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=(255, 255, 255, 30),
                        outline=(255, 255, 255, 46), width=2)
    r = h * 0.16
    d.ellipse([h * 0.45 - r, h / 2 - r, h * 0.45 + r, h / 2 + r], fill=(88, 214, 141, 255))
    d.text((h * 0.78, h / 2), text, font=f, fill=(236, 245, 240, 255), anchor="lm")
    return img


# ---------------------------------------------------------------------- renderer


class Renderer:
    def __init__(self, cap_dir, W, H, fps):
        self.tl = Timeline(cap_dir, W, H, fps)
        tl = self.tl
        self.pad = round(W * 0.08)
        self.back = backdrop(tl, self.pad)
        self.cursor, self.cursor_pad = cursor_sprite(256)
        self.bars = {}
        self.src_cache = (None, None)
        self.mask_cache = {}
        self.back_cache = (None, None)
        self.badges = {}
        self.out_cache = (None, None)
        self.cut_cache = {}
        chap = [(c["t"], c["name"]) for c in tl.chapters]
        self.chap_t = [c[0] for c in chap]
        self.chap_name = [c[1] for c in chap]

    def src(self, idx):
        if self.src_cache[0] != idx:
            im = Image.open(self.tl.frame_files[idx]).convert("RGB")
            if im.size != (SRC_W, SRC_H):
                im = im.resize((SRC_W, SRC_H), Image.LANCZOS)
            self.src_cache = (idx, im)
        return self.src_cache[1]

    def bar(self, kind, text):
        key = (kind, text)
        if key not in self.bars:
            self.bars[key] = title_bar(kind, text, SRC_W)
        return self.bars[key]

    def state(self, f):
        tl = self.tl
        t = f / tl.fps
        scene, si = tl.scene_at(t)
        idx = tl.frame_index(t)
        first = scene.get("first", 0)
        if first < len(tl.frame_files) and idx < first:
            idx = first
        s, cx, cy = tl.cam[min(f, tl.n)]
        cam = (round(s, 4), round(cx, 1), round(cy, 1))
        cur = None
        press, ripples = 0.0, []
        if scene["kind"] == "browser":
            x, y, _ = tl.cursor_at(t)
            press, ripples = tl.press_at(t)
            cur = (round(x, 1), round(y, 1))
        text = scene.get("url") if scene["kind"] == "browser" else scene.get("title")
        ci = bisect.bisect_right(self.chap_t, t) - 1
        chap = self.chap_name[ci] if ci >= 0 else None
        cut_t, _ = tl.cut_before(t)
        age = t - cut_t if cut_t is not None else 99
        return dict(t=t, kind=scene["kind"], text=text, idx=idx, cam=cam, cur=cur,
                    press=round(press, 3), ripples=[(x, y, round(a, 3)) for x, y, a in ripples],
                    chap=chap, age=round(min(age, 9), 3))

    def compose(self, st):
        key = (st["kind"], st["text"], st["idx"], st["cam"], st["cur"], st["press"],
               tuple(st["ripples"]), st["chap"], round(min(st["age"], 1.0), 3))
        if self.out_cache[0] == key:
            return self.out_cache[1]
        tl = self.tl
        W, H = tl.W, tl.H
        s, cx, cy = st["cam"]

        def X(x):
            return (x - cx) * s + W / 2

        def Y(y):
            return (y - cy) * s + H / 2

        if st["kind"] == "card":
            out = self.src(st["idx"]).resize((W, H), Image.LANCZOS)
        else:
            # backdrop through the camera
            bkey = st["cam"]
            if self.back_cache[0] != bkey:
                pad = self.pad
                box = ((0 - W / 2) / s + cx + pad, (0 - H / 2) / s + cy + pad,
                       (W - W / 2) / s + cx + pad, (H - H / 2) / s + cy + pad)
                bw, bh = self.back.size
                box = (max(0.0, box[0]), max(0.0, box[1]), min(bw, box[2]), min(bh, box[3]))
                self.back_cache = (bkey, self.back.resize((W, H), Image.BILINEAR, box=box))
            out = self.back_cache[1].copy()

            # window = title bar + content, mapped through the camera
            src = self.src(st["idx"])
            bar = self.bar(st["kind"], st["text"])
            wx0, wy0, wx1, wy1 = tl.win
            ox0, oy0, ox1, oy1 = X(wx0), Y(wy0), X(wx1), Y(wy1)
            vx0, vy0 = max(0, math.ceil(ox0)), max(0, math.ceil(oy0))
            vx1, vy1 = min(W, math.floor(ox1)), min(H, math.floor(oy1))
            if vx1 > vx0 and vy1 > vy0:
                sw, sh_ = SRC_W, SRC_H + BAR_SRC
                fx = sw / (ox1 - ox0)
                fy = sh_ / (oy1 - oy0)
                box = (max(0.0, (vx0 - ox0) * fx), max(0.0, (vy0 - oy0) * fy),
                       min(sw, (vx1 - ox0) * fx), min(sh_, (vy1 - oy0) * fy))
                win = self._window_src(src, bar, st["idx"], st["kind"], st["text"])
                resample = Image.BICUBIC if fx < 1.0 else Image.LANCZOS
                part = win.resize((vx1 - vx0, vy1 - vy0), resample, box=box)
                mask = self._mask((round(ox0, 1), round(oy0, 1), round(ox1, 1), round(oy1, 1)),
                                  (vx0, vy0, vx1, vy1), tl.radius * s)
                out.paste(part, (vx0, vy0), mask)

            if st["chap"] and st["kind"] != "card":
                a = max(0.0, min(1.0, (1.08 - s) / 0.08)) * ease_out((st["age"] - 0.3) / 0.5)
                if a > 0.01:
                    badge = self._badge(st["chap"])
                    b = badge.copy()
                    b.putalpha(b.getchannel("A").point(lambda v: int(v * a)))
                    by = max(4, int((wy0 - badge.height) / 2))
                    out.paste(b, (int(wx0), by), b)

            if st["cur"] is not None:
                out = self._draw_cursor(out, st, X, Y, s)

        self.out_cache = (key, out)
        return out

    def _window_src(self, src, bar, idx, kind, text):
        key = (idx, kind, text)
        if getattr(self, "_win_key", None) != key:
            win = Image.new("RGB", (SRC_W, SRC_H + BAR_SRC))
            win.paste(bar, (0, 0))
            win.paste(src, (0, BAR_SRC))
            self._win_key, self._win = key, win
        return self._win

    def _mask(self, orect, vrect, radius):
        key = (orect, vrect, round(radius, 1))
        m = self.mask_cache.get(key)
        if m is None:
            ox0, oy0, ox1, oy1 = orect
            vx0, vy0, vx1, vy1 = vrect
            S = 2
            w, h = vx1 - vx0, vy1 - vy0
            big = Image.new("L", (w * S, h * S), 0)
            ImageDraw.Draw(big).rounded_rectangle(
                [(ox0 - vx0) * S, (oy0 - vy0) * S, (ox1 - vx0) * S - 1, (oy1 - vy0) * S - 1],
                radius=radius * S, fill=255)
            m = big.resize((w, h), Image.BILINEAR)
            if len(self.mask_cache) > 32:
                self.mask_cache.clear()
            self.mask_cache[key] = m
        return m

    def _badge(self, name):
        if name not in self.badges:
            self.badges[name] = chapter_badge(name, self.tl.W)
        return self.badges[name]

    def _draw_cursor(self, out, st, X, Y, s):
        tl = self.tl
        css = tl.cw / tl.vw
        # ripples
        for rx, ry, age in st["ripples"]:
            u = age / 0.6
            sx, sy = tl.css_to_stage(rx, ry)
            px, py = X(sx), Y(sy)
            r = (10 + 30 * ease_out(u)) * css * s
            alpha = int(200 * (1 - u) ** 1.5)
            if alpha <= 2:
                continue
            S = 2
            size = int(2 * r + 12)
            patch = Image.new("RGBA", (size * S, size * S), (0, 0, 0, 0))
            d = ImageDraw.Draw(patch)
            c = size * S / 2
            d.ellipse([c - r * S, c - r * S, c + r * S, c + r * S],
                      fill=(255, 214, 90, alpha // 4), outline=(255, 214, 90, alpha),
                      width=max(2, int(2.2 * css * s * S)))
            patch = patch.resize((size, size), Image.BILINEAR)
            out.paste(patch, (int(px - size / 2), int(py - size / 2)), patch)
        # pointer
        x, y = st["cur"]
        sx, sy = tl.css_to_stage(x, y)
        px, py = X(sx), Y(sy)
        scale = CURSOR_CSS * css * s * (1 - 0.16 * st["press"]) / 256
        w = max(1, int(self.cursor.width * scale))
        h = max(1, int(self.cursor.height * scale))
        spr = self.cursor.resize((w, h), Image.LANCZOS)
        pad = self.cursor_pad * scale
        out.paste(spr, (int(round(px - pad)), int(round(py - pad))), spr)
        return out

    def frame(self, f):
        st = self.state(f)
        img = self.compose(st)
        age = st["age"]
        tl = self.tl
        if age < XFADE:
            cut_t, ci = tl.cut_before(st["t"])
            if ci and ci > 0:
                prev_f = int(math.floor(cut_t * tl.fps)) - 1
                if prev_f >= 0:
                    if ci not in self.cut_cache:
                        saved = self.out_cache
                        self.cut_cache = {ci: self.compose(self.state(prev_f)).copy()}
                        self.out_cache = saved
                    prev = self.cut_cache[ci]
                    img = Image.blend(prev, img, ease_io(age / XFADE))
        return img


# ------------------------------------------------------------------------ encode

_R = None


def _render_chunk(job):
    global _R
    cap_dir, W, H, fps, a, b, path = job
    if _R is None:
        _R = Renderer(cap_dir, W, H, fps)
    tmp = path + ".part.mp4"
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
           "-vf", "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuv420p",
           "-c:v", "libx264", "-preset", "slow", "-crf", "14", "-profile:v", "high",
           "-g", str(fps // 2), "-bf", "2", "-x264-params", "threads=3",
           "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
           "-color_range", "tv", tmp]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    last = None
    for f in range(a, b):
        img = _R.frame(f)
        if img is not last:
            buf = img.tobytes()
            last = img
        p.stdin.write(buf)
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError(f"ffmpeg failed on chunk {a}-{b}")
    os.replace(tmp, path)
    return path


def render_video(cap_dir, out_dir, W, H, fps, jobs, chunk_secs=12):
    tl = Timeline(cap_dir, W, H, fps)
    os.makedirs(out_dir, exist_ok=True)
    step = int(chunk_secs * fps)
    work = []
    for i, a in enumerate(range(0, tl.n, step)):
        path = os.path.join(out_dir, f"chunk_{W}x{H}_{i:04d}.mp4")
        if not os.path.exists(path):
            work.append((cap_dir, W, H, fps, a, min(tl.n, a + step), path))
    total = len(range(0, tl.n, step))
    print(f"video: {tl.n} frames, {total} chunks, {len(work)} to render")
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        for i, p in enumerate(ex.map(_render_chunk, work), 1):
            print(f"  chunk {i}/{len(work)} done", flush=True)
    chunks = [os.path.join(out_dir, f"chunk_{W}x{H}_{i:04d}.mp4") for i in range(total)]
    listing = os.path.join(out_dir, f"chunks_{W}x{H}.txt")
    with open(listing, "w") as fh:
        fh.writelines(f"file '{c}'\n" for c in chunks)
    return tl, listing


# ------------------------------------------------------------------------- audio

AR = 48000


def _upsample(x, sr):
    if sr == AR:
        return x
    n = int(round(x.size * AR / sr))
    X = np.fft.rfft(x)
    Y = np.zeros(n // 2 + 1, complex)
    m = min(X.size, Y.size)
    Y[:m] = X[:m]
    return np.fft.irfft(Y, n) * (n / x.size)


def render_audio(tl, out_wav):
    import music

    dur = tl.n / tl.fps
    n = int(dur * AR) + AR
    voice = np.zeros(n, np.float32)
    active = np.zeros(int(dur * 100) + 200, np.float32)  # 100 Hz control rate
    for e in tl.says:
        x, sr = sf.read(e["wav"], dtype="float32")
        if x.ndim > 1:
            x = x.mean(1)
        x = _upsample(x, sr).astype(np.float32)
        rms = np.sqrt(np.mean(x ** 2)) + 1e-9
        x *= 10 ** (-19 / 20) / rms  # consistent level per line
        a = int(e["t"] * AR)
        b = min(n, a + x.size)
        voice[a:b] += x[: b - a]
        active[int(e["t"] * 100): int((e["t"] + e["dur"]) * 100) + 1] = 1
    # gentle high-pass on the voice (below ~70 Hz)
    V = np.fft.rfft(voice)
    fr = np.fft.rfftfreq(voice.size, 1 / AR)
    V *= np.clip((fr - 50) / 40, 0, 1)
    voice = np.fft.irfft(V, voice.size).astype(np.float32)

    # ducking envelope: music at full bed level between lines, down under the voice
    bed, ducked = 0.36, 0.13
    g = np.where(active > 0, ducked, bed).astype(np.float32)
    env = np.empty_like(g)
    level = bed
    for i, v in enumerate(g):  # fast attack (duck), slow release
        coef = 0.25 if v < level else 0.035
        level += (v - level) * coef
        env[i] = level
    # look-ahead: start ducking ~150 ms before each line
    env = np.minimum(env, np.concatenate([env[15:], np.full(15, env[-1])]))
    tt = np.arange(n) / AR
    gain = np.interp(tt, np.arange(env.size) / 100, env).astype(np.float32)

    m = music.render(dur + 1.0)[:n]
    if m.shape[0] < n:
        m = np.pad(m, ((0, n - m.shape[0]), (0, 0)))
    mix = m * gain[:, None] + voice[:, None]
    mix = mix[: int(dur * AR)]
    raw = out_wav + ".raw.wav"
    sf.write(raw, mix, AR, subtype="FLOAT")

    # two-pass EBU R128 loudness normalization to YouTube's -14 LUFS
    ff = ffmpeg_exe()
    probe = subprocess.run([ff, "-hide_banner", "-i", raw, "-af",
                            "loudnorm=I=-14:TP=-1.0:LRA=11:print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True).stderr
    js = json.loads(probe[probe.rindex("{"): probe.rindex("}") + 1])
    subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", raw, "-af",
                    "loudnorm=I=-14:TP=-1.0:LRA=11:linear=true:"
                    f"measured_I={js['input_i']}:measured_TP={js['input_tp']}:"
                    f"measured_LRA={js['input_lra']}:measured_thresh={js['input_thresh']}:"
                    f"offset={js['target_offset']},aresample=48000",
                    "-c:a", "pcm_s24le", out_wav], check=True)
    os.remove(raw)
    return out_wav


# ------------------------------------------------------------- subtitles, chapters


def fmt_srt(t):
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _split(text, limit=84):
    parts = re.split(r"(?<=[.:!?,;])\s+", text)
    out, cur = [], ""
    for p in parts:
        if cur and len(cur) + 1 + len(p) > limit:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        out.append(cur)
    return out


def _wrap(line, width=46):
    if len(line) <= width:
        return line
    cut = line.rfind(" ", 0, len(line) // 2 + 8)
    return line[:cut] + "\n" + line[cut + 1:] if cut > 0 else line


def write_srt(tl, path):
    n = 1
    with open(path, "w") as fh:
        for e in tl.says:
            pieces = _split(e["text"])
            total = sum(len(p) for p in pieces)
            t = e["t"]
            for p in pieces:
                d = e["dur"] * len(p) / total
                fh.write(f"{n}\n{fmt_srt(t)} --> {fmt_srt(t + d)}\n{_wrap(p)}\n\n")
                n += 1
                t += d


def write_chapters(tl, path):
    lines = []
    for i, c in enumerate(tl.chapters):
        t = 0 if i == 0 else int(c["t"])
        lines.append(f"{t // 60}:{t % 60:02d} {c['name']}")
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return lines


# --------------------------------------------------------------------- produce


def produce(cap_dir, out, height=2160, fps=60, jobs=8):
    W, H = height * 16 // 9, height
    work = os.path.join(os.path.dirname(cap_dir), "render")
    tl, listing = render_video(cap_dir, work, W, H, fps, jobs)
    wav = render_audio(tl, os.path.join(work, "mix.wav"))
    ff = ffmpeg_exe()
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", listing, "-i", wav,
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "384k", "-ar", "48000",
                    "-shortest", "-movflags", "+faststart", out], check=True)
    base = os.path.splitext(out)[0]
    write_srt(tl, base + ".srt")
    chapters = write_chapters(tl, base + ".chapters.txt")
    print("wrote", out)
    print("\n".join(chapters))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("out")
    ap.add_argument("--height", type=int, default=2160)
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--still", type=float, nargs="*",
                    help="only write PNG stills at these times (for review)")
    a = ap.parse_args()
    if a.still:
        r = Renderer(a.capture, a.height * 16 // 9, a.height, a.fps)
        for t in a.still:
            p = f"{os.path.splitext(a.out)[0]}_{t:07.2f}.png"
            r.frame(int(t * a.fps)).save(p)
            print(p)
    else:
        produce(a.capture, a.out, a.height, a.fps, a.jobs)
