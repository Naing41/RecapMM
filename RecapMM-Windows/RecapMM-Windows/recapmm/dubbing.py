"""Dubbing Studio: the person's OWN narration file is the master clock; the picture is re-timed to it (port of VoiceSync.kt).
The voice is never stretched or moved. Needs no TTS. Whisper listens to the source, the voice is cut into phrases at its pauses,
phrases are matched with the English speech units (length-based alignment) and every matched group's picture is played across
the group's audio."""
import json
import os
import re
import shutil
from math import ceil

import numpy as np

from . import media, units
from .gemini import Stopped
from .pipeline import LANGS, job_dir, safe_name, transcribe

FRAME = 0.01
SPEED_MIN = 1.0 / 1.35      # slower than this, short freeze holds are spread over the shot
MAX_HOLD = 0.75
SPEED_MAX = 1.25            # faster than this the rest of that picture is cut
HOLD_BACK = 0.04
RATE = 24000


class Phrase:
    def __init__(self, start, end):
        self.start, self.end = start, end

    @property
    def dur(self):
        return self.end - self.start


class Bead:
    def __init__(self, i0, i1, j0, j1):
        self.i0, self.i1, self.j0, self.j1 = i0, i1, j0, j1


# ---------------------------------------------------------------- voice analysis
def envelope(pcm, rate=RATE):
    """Level (dBFS) of every 10 ms of an int16 mono array."""
    win = rate // 100
    n = len(pcm) // win
    if n == 0:
        return np.zeros(0, np.float32)
    x = np.asarray(pcm[:n * win], dtype=np.float64).reshape(n, win)
    rms = np.sqrt((x ** 2).mean(axis=1)) / 32768.0
    return (20.0 * np.log10(np.maximum(rms, 1e-5))).astype(np.float32)


def threshold(env):
    if len(env) == 0:
        return -50.0
    s = np.sort(env)
    p5 = float(s[min(max(int(len(s) * 0.05), 0), len(s) - 1)])
    p95 = float(s[min(max(int(len(s) * 0.95), 0), len(s) - 1)])
    return max(min(max(p5 + 10.0, p95 - 40.0), p95 - 18.0), -65.0)


def phrases(env, thr, min_gap):
    """Speech stretches separated by at least [min_gap] seconds of quiet."""
    n = len(env)
    gap_frames = int(ceil(min_gap / FRAME))
    above = np.concatenate([[0], (env > thr).astype(np.int8), [0]])
    d = np.diff(above)
    starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    merged = []
    for i, j in zip(starts, ends):
        if j - i < 3:                       # clicks shorter than 30 ms
            continue
        if merged and i - merged[-1][1] < gap_frames:
            merged[-1][1] = j
        else:
            merged.append([int(i), int(j)])
    total = n * FRAME
    return [Phrase(max(0.0, a * FRAME - 0.02), min(total, b * FRAME + 0.05)) for a, b in merged if (b - a) * FRAME >= 0.12]


def auto_phrases(env, english_units):
    """Tries several pause lengths and keeps the one whose phrase count is closest to the English unit count."""
    thr = threshold(env)
    best, best_gap, best_score = [], 0.3, 1e18
    for g in (0.15, 0.20, 0.27, 0.35, 0.45, 0.60, 0.80, 1.00, 1.30):
        p = phrases(env, thr, g)
        if not p:
            continue
        score = abs(np.log(len(p) / max(1, english_units)))
        if score < best_score - 1e-9:
            best, best_gap, best_score = p, g, score
    return best, best_gap


# ---------------------------------------------------------------- alignment
BEADS = [((1, 1), 0.80), ((1, 2), 0.06), ((2, 1), 0.06), ((1, 3), 0.012), ((3, 1), 0.012), ((2, 2), 0.02),
         ((1, 4), 0.004), ((4, 1), 0.004), ((2, 3), 0.008), ((3, 2), 0.008), ((1, 5), 0.001), ((5, 1), 0.001),
         ((1, 0), 0.003), ((0, 1), 0.003)]
INF = 1e18


def align_lengths(e_len, a_len):
    """Monotone length-based alignment (Gale-Church style). e_len = English speech seconds per unit, a_len = voice seconds per phrase.
    Returns beads covering every phrase and every unit exactly once, in order (or None)."""
    m, n = len(e_len), len(a_len)
    if n == 0 or m == 0 or n * m > 6_000_000:
        return None
    cum_e = np.concatenate([[0.0], np.cumsum(e_len)])
    cum_a = np.concatenate([[0.0], np.cumsum(a_len)])
    c = cum_a[n] / max(cum_e[m], 1e-6)
    prior = [-np.log(p) for _, p in BEADS]
    i01 = len(BEADS) - 1
    d = np.full((n + 1, m + 1), INF)
    bp = np.full((n + 1, m + 1), -1, dtype=np.int8)
    c01 = np.concatenate([[0.0], prior[i01] + 0.5 * np.asarray(e_len, dtype=np.float64)])
    cc = np.cumsum(c01)
    for i in range(n + 1):
        a = np.full(m + 1, INF)
        arg = np.full(m + 1, -1, dtype=np.int8)
        if i == 0:
            a[0] = 0.0
        for b, ((p, q), _) in enumerate(BEADS):
            if i < p or (p == 0 and q == 1):
                continue
            if q == 0:                                       # a phrase with no English counterpart
                prev = d[i - p, :]
                cand = prev + prior[b] + 0.5 * (cum_a[i] - cum_a[i - p])
                cand = np.where(prev >= INF, INF, cand)
                better = cand < a
                a = np.where(better, cand, a)
                arg = np.where(better, b, arg).astype(np.int8)
                continue
            if m + 1 <= q:
                continue
            prev = d[i - p, 0:m + 1 - q]                    # j - q for j = q..m
            l2 = cum_a[i] - cum_a[i - p]
            l1 = cum_e[q:m + 1] - cum_e[0:m + 1 - q]
            sd = np.sqrt(0.5 * np.maximum(l1, 0.5)) + 0.15
            z = (l2 - c * l1) / sd
            cost = prior[b] + 2.0 * (np.sqrt(1.0 + z * z) - 1.0)
            cand = np.where(prev >= INF, INF, prev + cost)
            seg_a, seg_arg = a[q:], arg[q:]
            better = cand < seg_a
            a[q:] = np.where(better, cand, seg_a)
            arg[q:] = np.where(better, b, seg_arg)
        # an English unit with no voice phrase: depends on the same row, so a running minimum scan
        s = cc + np.minimum.accumulate(a - cc)
        s = np.where(s >= INF / 2, INF, s)
        from0 = s < a - 1e-9
        d[i, :] = np.where(from0, s, a)
        arg = np.where(from0, i01, arg).astype(np.int8)
        bp[i, :] = arg
    if d[n, m] >= INF / 2:
        return None
    beads, i, j = [], n, m
    while i > 0 or j > 0:
        b = int(bp[i, j])
        if b < 0:
            return None
        p, q = BEADS[b][0]
        beads.append(Bead(i - p, i, j - q, j))
        i -= p
        j -= q
    beads.reverse()
    return beads


class _Slice:
    def __init__(self, o0, o1, v0, v1):
        self.o0, self.o1, self.v0, self.v1 = o0, o1, v0, v1


def to_plan(slices, stats):
    """Slices (output seconds -> source seconds) -> plan tuples (o0, o1, src_start, speed). Exact fit whenever the needed speed is
    within [SPEED_MIN, SPEED_MAX]; otherwise the picture is cut (too fast) or held (too slow)."""
    out = []
    for s in slices:
        dur = s.o1 - s.o0
        if dur <= 1e-6:
            continue
        w = max(0.0, s.v1 - s.v0)
        need = w / dur
        if need > SPEED_MAX:
            out.append((s.o0, s.o1, s.v0, SPEED_MAX))
            stats[0] += 1
        elif need >= SPEED_MIN:
            out.append((s.o0, s.o1, s.v0, need))
        else:
            stats[1] += 1
            if w < 0.2:
                out.append((s.o0, s.o1, max(0.0, s.v1 - HOLD_BACK), 0.0))
            else:
                freeze = dur - w / SPEED_MIN
                n = min(24, max(1, int(ceil(freeze / MAX_HOLD))))
                hold, chunk = freeze / n, w / n
                t = s.o0
                for i in range(n):
                    a = s.v0 + i * chunk
                    b = a + chunk
                    tp = chunk / SPEED_MIN
                    out.append((t, t + tp, a, SPEED_MIN))
                    t += tp
                    end = s.o1 if i == n - 1 else t + hold
                    out.append((t, end, max(a, b - HOLD_BACK), 0.0))
                    t = end
    return out


def align(env, audio_dur, segs, video_dur, lead, log):
    phr, gap = auto_phrases(env, len(segs))
    if not phr:
        raise RuntimeError("Voice ဖိုင်ထဲ စကားပြောသံ မတွေ့ပါ (အသံတိတ်နေလား စစ်ပါ)")
    log("🎙 voice %.1f မိနစ် · phrase %d ခု (pause ≥ %.2fs) · English unit %d ခု" % (audio_dur / 60, len(phr), gap, len(segs)))
    e_len = np.array([max(0.2, s.end - s.start) for s in segs])
    a_len = np.array([p.dur for p in phr])
    beads = align_lengths(e_len, a_len)
    fallback = False
    if beads is None:
        fallback = True
        beads = [Bead(0, len(phr), 0, len(segs))]
        log("⚠️ phrase ကို တစ်ခုချင်း မကိုက်ညှိနိုင် — တစ်လျှောက်လုံး အချိုးကျ ချိန်ပါမယ်")

    def cut_time(i0):
        if i0 <= 0:
            return 0.0
        ps, pe = phr[i0].start, phr[i0 - 1].end
        return ps if pe >= ps else min(max(ps - lead, pe), ps)

    audio_beads = [b for b in beads if b.i1 > b.i0]
    slices = []
    for k, b in enumerate(audio_beads):
        o0 = cut_time(b.i0)
        o1 = cut_time(audio_beads[k + 1].i0) if k + 1 < len(audio_beads) else audio_dur
        if b.j1 > b.j0:
            v0, v1 = segs[b.j0].vstart, segs[b.j1 - 1].vend
        else:                                  # phrase with no English counterpart: stay on the picture we just left
            v0 = slices[-1].v1 if slices else 0.0
            v1 = v0
        if o1 - o0 < 0.1 and slices:           # a bead too short to see is folded into the one before it
            slices[-1].o1 = o1
            slices[-1].v1 = max(slices[-1].v1, v1)
        else:
            slices.append(_Slice(o0, o1, v0, max(v0, v1)))
    if not slices:
        slices.append(_Slice(0.0, audio_dur, 0.0, video_dur))
    slices[0].o0 = 0.0
    slices[-1].o1 = audio_dur
    stats = [0, 0]
    plan = to_plan(slices, stats)
    if not plan:
        raise RuntimeError("Sync plan ထုတ်မရပါ")
    speeds = [p[3] for p in plan if p[3] > 0]
    summary = "⏱ sync slice %d ခု · ရုပ်အမြန်နှုန်း %.2f×–%.2f× · ဖြတ်ထုတ် %d · freeze %d shot%s" % (
        len(slices), min(speeds or [1.0]), max(speeds or [1.0]), stats[0], stats[1], " · (အချိုးကျ)" if fallback else "")
    return plan, summary, {"audio_duration": audio_dur, "video_duration": video_dur, "pause_gap": gap,
                           "phrases": [[p.start, p.end] for p in phr], "plan": [list(p) for p in plan]}


# ---------------------------------------------------------------- the tool
def run_dubbing(cfg, source, voice_file, lead_ms, out_dir, log, stage, prog, cancelled, jobs_root):
    """Source video + the person's own narration file -> the video re-timed to the narration (.mp4)."""
    if not media.check_ffmpeg():
        raise RuntimeError("ffmpeg မတွေ့ပါ — ffmpeg.exe ကို app နဲ့ folder တူတူ ထားပါ")
    info = media.probe(source)
    dur = info["duration"]
    work = job_dir(jobs_root, source)

    stage("အသံထုတ်ယူ", 0.02)
    audio = media.audio_16k(source)
    prog(0.05)
    stage("မူရင်းအသံ နားထောင် (Whisper)", 0.05)
    wf = os.path.join(work, "words.json")
    if os.path.isfile(wf):
        words = [units.Word(**w) for w in json.load(open(wf, encoding="utf-8"))]
    else:
        words = transcribe(audio, cfg["whisper"], LANGS.get(cfg["lang"], "en"), log, lambda f: prog(0.05 + 0.40 * f), cancelled)
        json.dump([w.__dict__ for w in words], open(wf, "w", encoding="utf-8"), ensure_ascii=False)
    del audio
    segs = units.drop_loops(units.atomicize(words))
    if not segs:
        raise RuntimeError("%s စကားပြောသံ မတွေ့ပါ" % cfg["lang"])
    units.assign_coverage(segs, dur)

    stage("Voice ဖိုင် ဖတ်နေတယ်", 0.46)
    try:
        voice = media.audio_pcm(voice_file, RATE)
    except IOError:
        raise IOError("Voice ဖိုင် ဖတ်မရပါ (mp3 / m4a / wav / aac သုံးပါ)")
    audio_dur = len(voice) / float(RATE)
    if audio_dur < 1.0:
        raise IOError("Voice ဖိုင် တိုလွန်းနေတယ်")
    env = envelope(voice)
    if cancelled():
        raise Stopped()

    stage("ရုပ်ကို voice နဲ့ ကိုက်ညှိ", 0.50)
    plan, summary, js = align(env, audio_dur, segs, dur, lead_ms / 1000.0, log)
    json.dump(js, open(os.path.join(work, "sync_plan.json"), "w", encoding="utf-8"))
    log(summary)

    gain = int(cfg["volume_pct"]) / 100.0
    wav = os.path.join(work, "narration.wav")
    media.write_wav(wav, np.clip(voice.astype(np.float32) * gain, -32768, 32767).astype(np.int16), RATE)

    stage("ရုပ် render (Smart Sync)", 0.52)
    seg_dir = os.path.join(work, "dub_segments")
    shutil.rmtree(seg_dir, ignore_errors=True)
    video_only = os.path.join(work, "dub_video_only.mp4")
    media.render_video(source, plan, video_only, seg_dir, info, log, lambda f: prog(0.52 + 0.44 * f), cancelled,
                       max_height=int(cfg["max_height"]), workers=int(cfg["workers"]))

    stage("ပေါင်းစပ်", 0.97)
    base = safe_name(os.path.splitext(os.path.basename(source))[0])
    os.makedirs(out_dir, exist_ok=True)
    final = os.path.join(out_dir, "%s_dub.mp4" % base)
    media.mux(video_only, wav, final)
    shutil.rmtree(seg_dir, ignore_errors=True)
    for p in (video_only, wav):
        try:
            os.remove(p)
        except OSError:
            pass
    prog(1.0)
    log("✅ " + final)
    return final
