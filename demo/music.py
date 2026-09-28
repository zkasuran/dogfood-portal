"""Original background music for the demo video, synthesized from scratch with numpy.

Nothing is sampled or downloaded, so the track is free of third-party rights and
will not trip YouTube Content ID. It is a calm, low-tempo electronic bed: warm pads,
a plucked arpeggio with ping-pong delay, a round sub bass and soft half-time drums,
with an intro, a couple of drum breakdowns and a clean outro.

    python3 demo/music.py 300 music.wav     # 300 seconds
"""
import sys

import numpy as np
import soundfile as sf

SR = 48000
BPM = 88.0
BEAT = 60.0 / BPM
BAR = 4 * BEAT

# Two bars per chord: Am9, Fmaj9, Cadd9, G6/B-ish. (bass midi, pad midis)
PROGRESSION = [
    (45, [57, 60, 64, 67, 71]),
    (41, [53, 57, 60, 64, 67]),
    (48, [55, 60, 62, 64, 67]),
    (43, [55, 59, 62, 64, 69]),
]


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12.0)


def _table(harmonics, rolloff):
    n = 4096
    ph = np.arange(n) / n
    w = sum(np.sin(2 * np.pi * k * ph) / k ** rolloff for k in range(1, harmonics + 1))
    return (w / np.abs(w).max()).astype(np.float32)


PAD_TABLE = _table(10, 1.6)
PLUCK_TABLE = _table(4, 2.2)


def osc(table, freq, n, phase0=0.0):
    ph = (phase0 + freq * np.arange(n) / SR) % 1.0
    return table[(ph * table.size).astype(np.int32)]


def adsr(n, a, r, sustain=1.0):
    env = np.full(n, sustain, dtype=np.float32)
    na, nr = min(n, int(a * SR)), min(n, int(r * SR))
    env[:na] = np.linspace(0, sustain, na, dtype=np.float32) if na else env[:na]
    if nr:
        env[-nr:] *= np.linspace(1, 0, nr, dtype=np.float32)
    return env


def add(buf, start, sig, pan=0.0, gain=1.0):
    """Mix a mono signal into the stereo buffer at sample `start` with equal-power pan."""
    s = int(start)
    if s >= buf.shape[0]:
        return
    sig = sig[: buf.shape[0] - s]
    lg = np.cos((pan + 1) * np.pi / 4) * gain
    rg = np.sin((pan + 1) * np.pi / 4) * gain
    buf[s : s + sig.size, 0] += sig * lg
    buf[s : s + sig.size, 1] += sig * rg


def reverb(x, seconds=2.6, seed=7):
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.zeros_like(x)
    for ch in range(2):
        ir = rng.standard_normal(n).astype(np.float32) * np.exp(-t * 6.9 / seconds)
        # darker tail: cheap one-pole low-pass on the impulse
        for _ in range(2):
            ir = np.concatenate([[ir[0]], 0.5 * ir[1:] + 0.5 * ir[:-1]])
        ir[: int(0.012 * SR)] = 0  # pre-delay
        ir /= np.sqrt((ir ** 2).sum())
        size = 1 << int(np.ceil(np.log2(x.shape[0] + n)))
        y = np.fft.irfft(np.fft.rfft(x[:, ch], size) * np.fft.rfft(ir, size), size)
        out[:, ch] = y[: x.shape[0]]
    return out


def render(duration):
    rng = np.random.default_rng(2026)
    n_total = int((duration + 4) * SR)
    pads = np.zeros((n_total, 2), np.float32)
    plucks = np.zeros((n_total, 2), np.float32)
    drums = np.zeros((n_total, 2), np.float32)
    bass = np.zeros((n_total, 2), np.float32)

    n_bars = int(np.ceil(duration / BAR)) + 1
    intro_bars = 4
    outro_start = duration - 2.5 * BAR
    # drum breakdowns roughly every 70 s, two bars long
    breaks = {b for b in range(intro_bars, n_bars) if b % 24 in (22, 23)}

    for bar in range(n_bars):
        t0 = bar * BAR
        if t0 > duration + 1:
            break
        root, notes = PROGRESSION[(bar // 2) % len(PROGRESSION)]
        drums_on = bar >= intro_bars and bar not in breaks and t0 < outro_start

        if bar % 2 == 0:  # pads span two bars, overlapping into the next chord
            n = int(2.25 * BAR * SR)
            for i, m in enumerate(notes):
                for det, pan in ((-0.09, -0.6), (0.0, 0.0), (0.08, 0.6)):
                    f = hz(m + det * 0.1) * (1 + det * 0.004)
                    trem = 1 + 0.12 * np.sin(2 * np.pi * (0.13 + 0.03 * i) * np.arange(n) / SR
                                            + rng.uniform(0, 6.28))
                    sig = osc(PAD_TABLE, f, n, rng.uniform()) * trem * adsr(n, 1.6, 1.8)
                    add(pads, t0 * SR, sig, pan=pan * 0.8, gain=0.030)

        # sub bass: beat 1 and the "and" of 3
        if bar >= 2:
            for beat, length in ((0, 2.2), (2.5, 1.3)):
                n = int(length * BEAT * SR)
                f = hz(root - 12 if root > 44 else root)
                sig = np.tanh(1.6 * osc(PLUCK_TABLE, f, n)) * adsr(n, 0.02, 0.25)
                add(bass, (t0 + beat * BEAT) * SR, sig, gain=0.085)

        # plucked arpeggio, 8th notes, from bar 2 on
        if bar >= 2:
            pattern = [0, 2, 4, 3, 1, 3, 4, 2] if bar % 2 == 0 else [0, 3, 4, 2, 1, 4, 3, 2]
            for step, idx in enumerate(pattern):
                if step in (5,) and bar % 4 == 3:
                    continue
                m = notes[idx] + 12
                n = int(1.2 * SR)
                tt = np.arange(n) / SR
                env = np.exp(-tt / 0.22) * np.minimum(1, tt / 0.004)
                sig = (osc(PLUCK_TABLE, hz(m), n) + 0.3 * np.sin(2 * np.pi * hz(m) * 2 * tt)) * env
                vel = 0.55 + 0.25 * (step % 2 == 0) + rng.uniform(-0.08, 0.08)
                add(plucks, (t0 + step * BEAT / 2) * SR, sig.astype(np.float32),
                    pan=rng.uniform(-0.35, 0.35), gain=0.050 * vel)

        if drums_on:
            for beat in (0, 2):  # half-time kick
                n = int(0.45 * SR)
                tt = np.arange(n) / SR
                f = 45 + 75 * np.exp(-tt / 0.035)
                sig = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt / 0.16)
                add(drums, (t0 + beat * BEAT) * SR, sig.astype(np.float32), gain=0.24)
            if bar % 2 == 1:
                n = int(0.45 * SR)
                tt = np.arange(n) / SR
                f = 45 + 75 * np.exp(-tt / 0.035)
                sig = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt / 0.16)
                add(drums, (t0 + 3.5 * BEAT) * SR, sig.astype(np.float32), gain=0.14)
            for beat in (1, 3):  # soft snare / rim
                n = int(0.25 * SR)
                tt = np.arange(n) / SR
                noise = rng.standard_normal(n).astype(np.float32)
                noise = noise - np.concatenate([[0], noise[:-1]]) * 0.6
                sig = noise * np.exp(-tt / 0.07) * 0.5 + np.sin(2 * np.pi * 190 * tt) * np.exp(-tt / 0.05)
                add(drums, (t0 + beat * BEAT) * SR, sig.astype(np.float32), pan=0.05, gain=0.07)
            for step in range(8):  # hats on 8ths
                n = int(0.08 * SR)
                tt = np.arange(n) / SR
                noise = rng.standard_normal(n).astype(np.float32)
                hp = noise - np.concatenate([[0], noise[:-1]])
                hp = hp - np.concatenate([[0], hp[:-1]])
                vel = 0.9 if step % 2 == 1 else 0.5
                add(drums, (t0 + step * BEAT / 2 + 0.008) * SR,
                    (hp * np.exp(-tt / 0.018)).astype(np.float32), pan=0.25, gain=0.012 * vel)

    # gentle sidechain pump on pads and bass from the half-time kick
    t = np.arange(n_total) / SR
    phase = (t % (2 * BEAT)) / (2 * BEAT)
    pump = 1 - 0.35 * np.exp(-phase * 2 * BEAT / 0.12)
    in_drums = np.array([
        (int(x // BAR) >= intro_bars and int(x // BAR) not in breaks and x < outro_start)
        for x in np.arange(0, n_total / SR, BAR)
    ])
    mask = in_drums[np.minimum((t // BAR).astype(int), in_drums.size - 1)]
    pump = np.where(mask, pump, 1.0).astype(np.float32)[:, None]

    # ping-pong delay on the plucks (dotted 8th)
    d = int(0.75 * BEAT * SR)
    delayed = np.zeros_like(plucks)
    fb = 0.0
    for k in range(1, 5):
        g = 0.42 ** k
        ch = k % 2
        delayed[d * k :, ch] += (plucks[: -d * k, 0] + plucks[: -d * k, 1]) * 0.5 * g
    plucks = plucks + delayed * (1 - fb)

    wet = reverb(pads * 0.6 + plucks * 0.8)
    mix = pads * pump + plucks + bass * pump + drums + wet * 0.55

    mix = mix[: int(duration * SR)]
    # high-pass below ~35 Hz (FFT domain): no rumble eating the headroom
    for ch in range(2):
        spec = np.fft.rfft(mix[:, ch])
        f = np.fft.rfftfreq(mix.shape[0], 1 / SR)
        spec *= np.clip((f - 25) / 20, 0, 1)
        mix[:, ch] = np.fft.irfft(spec, mix.shape[0])
    fade_in, fade_out = int(2.5 * SR), int(6.0 * SR)
    mix[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    mix[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None] ** 1.5
    mix = np.tanh(mix * 1.1) / 1.1
    mix /= max(1e-9, np.abs(mix).max()) / 0.89
    return mix.astype(np.float32)


if __name__ == "__main__":
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60
    out = sys.argv[2] if len(sys.argv) > 2 else "music.wav"
    sf.write(out, render(secs), SR, subtype="PCM_24")
    print("wrote", out)
