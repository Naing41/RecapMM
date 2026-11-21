"""Movie Recap (port of MovieRecap.kt): the WHOLE movie is analysed (all dialogue + frames from the whole film) -> story, characters,
key points. The recap is built around the chosen points: for each one the AI looks closely at that part (a frame every few seconds) and
writes the Burmese narration with the exact footage of each line; a voice (Edge / Google / your Voice Clone) speaks it; the picture of
every line is cut from the movie as long as its voice. Everything is joined into one video. Every stage caches its result in the
movie's job folder, so a stopped / failed run carries on where it was."""
import hashlib
import json
import os
import pickle
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from math import ceil, sqrt

import numpy as np

from . import clone, media, units
from .gemini import DEFAULT_MODELS, Gemini, Stopped
from .pipeline import LANGS, safe_name, transcribe
from .tools import split_text
from .tts import EdgeTts, GoogleTts, RATE, VOICES

SAMPLE = "ဒီရုပ်ရှင်ထဲမှာ ဇာတ်လိုက်က မိသားစုကို ကယ်တင်ဖို့ ကြိုးစားနေပေမယ့် အခြေအနေတွေက တဖြည်းဖြည်း ပိုဆိုးလာတယ်။ နောက်ဆုံးမှာတော့ သူ့ရဲ့ လျှို့ဝှက်ချက်တွေ ပေါ်လာတော့တယ်။"
TS_RE = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


# ---------------------------------------------------------------- small helpers
def auto_minutes(kept_sec):
    """Recap length from the kept length of the movie (a 4 hour film -> 16 min, 2 hours -> 11-12 min)."""
    return int(min(25, max(5, round(1.05 * sqrt(kept_sec / 60.0)))))


def select(imps, target_sec, include=None):
    """Which key points are told: the opening and the ending always, then by importance (spread over the film)."""
    n = len(imps)
    if include is not None:
        return [i in include for i in range(n)]
    if n == 0:
        return []
    k = min(n, max(min(6, n), int(target_sec / 11.0)))
    chosen = {0, n - 1}
    for lvl in (5, 4, 3, 2, 1):
        cand = [i for i in range(n) if imps[i] == lvl and i not in chosen]
        room = k - len(chosen)
        if room <= 0:
            break
        if len(cand) <= room:
            chosen.update(cand)
        else:
            for j in range(room):
                chosen.add(cand[min(int(((j + 0.5) * len(cand)) / room), len(cand) - 1)])
    return [i in chosen for i in range(n)]


def seconds(imps, sel, target_sec):
    """Narration seconds of every key point (0 when not selected): main points get the most time."""
    w = lambda i: {5: 3.0, 4: 2.2, 3: 1.4, 2: 0.9}.get(i, 0.6)
    total = sum(w(imps[i]) for i in range(len(imps)) if sel[i])
    return [0.0 if (not sel[i] or total <= 0) else min(70.0, max(6.0, target_sec * w(imps[i]) / total)) for i in range(len(imps))]


def hms(sec):
    t = max(0, int(sec))
    return "%d:%02d:%02d" % (t // 3600, (t // 60) % 60, t % 60)


def parse_time(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        p = []
        for x in v.strip().split(":"):
            try:
                p.append(float(x.strip()))
            except ValueError:
                pass
        return {3: lambda: p[0] * 3600 + p[1] * 60 + p[2], 2: lambda: p[0] * 60 + p[1], 1: lambda: p[0]}.get(len(p), lambda: 0.0)()
    return 0.0


def read_srt_text(path):
    """Subtitle file in whatever encoding it was saved in: UTF-8 / UTF-16, or an old Thai (Windows-874) / Western file."""
    b = open(path, "rb").read()
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16")
    try:
        return b.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    for enc in ("cp874", "cp1252"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "replace")


def parse_srt(text):
    sec = lambda h, m, s, ms: float(h) * 3600 + float(m) * 60 + float(s) + float("0." + ms)
    out = []
    for blk in re.split(r"\n\s*\n", text.replace("\r", "").replace("\ufeff", "")):
        lines = blk.strip().split("\n")
        ti = next((i for i, l in enumerate(lines) if TS_RE.search(l)), -1)
        if ti < 0:
            continue
        g = TS_RE.search(lines[ti]).groups()
        txt = units.clean(re.sub(r"\{[^}]*\}", "", re.sub(r"<[^>]+>", "", " ".join(lines[ti + 1:]))))
        if txt:
            out.append(units.Seg(len(out), sec(*g[0:4]), sec(*g[4:8]), txt))
    return out


def endings_too_same(points):
    """One sentence ending makes up >45% of the narration lines, or repeats 3+ times in a row."""
    ends = []
    for p in points:
        for ln in (p.get("lines") or []) if isinstance(p, dict) else []:
            t = str(ln.get("text", "")).strip().rstrip("။၊ ") if isinstance(ln, dict) else ""
            if len(t) >= 4:
                ends.append(t[-3:])
    if len(ends) < 4:
        return False
    counts = {}
    for e in ends:
        counts[e] = counts.get(e, 0) + 1
    if max(counts.values()) > len(ends) * 0.45:
        return True
    return any(ends[i] == ends[i - 1] == ends[i - 2] for i in range(2, len(ends)))


def polish_speech(pcm, rate=RATE, keep_ms=50, target_rms=2600.0):
    """Cuts the silence the voice leaves at both ends of a clip, evens the loudness, tiny fades (no clicks)."""
    if len(pcm) < rate // 10:
        return pcm
    frame = rate // 100
    n = len(pcm) // frame
    x = pcm[:n * frame].astype(np.float64).reshape(n, frame)
    rms = np.sqrt((x ** 2).mean(axis=1))
    loud = float(np.sort(rms)[::-1][:max(1, n // 5)].mean())
    thr = max(40.0, loud * 0.06)
    idx = np.nonzero(rms > thr)[0]
    if len(idx) == 0:
        return pcm
    keep = keep_ms // 10
    a, b = max(0, idx[0] - keep), min(n - 1, idx[-1] + keep)
    out = pcm[a * frame:min(len(pcm), (b + 1) * frame)].astype(np.float64)
    cur = max(1.0, float(np.sqrt((out ** 2).mean())))
    peak = max(1.0, float(np.abs(out).max()))
    gain = min(31000.0 / peak, min(2.5, max(0.6, target_rms / cur)))
    g = np.full(len(out), gain)
    fade = rate * 6 // 1000
    if len(out) > 2 * fade:
        ramp = np.arange(fade) / float(fade)
        g[:fade] *= ramp
        g[-fade:] *= ramp[::-1]
    return np.clip(out * g, -32768, 32767).astype(np.int16)


def extract_frames(source, a, b, step, cache_dir, cancelled):
    """JPEG frames of [a, b] (one every [step] seconds, labelled with their movie time) for the AI to look at. Cached."""
    key = hashlib.sha1(("%s|%d|%.1f|%.1f|%.2f" % (source, os.path.getsize(source), a, b, step)).encode()).hexdigest()[:12]
    cache = os.path.join(cache_dir, "frames_%s.pkl" % key)
    if os.path.isfile(cache):
        try:
            return pickle.load(open(cache, "rb"))
        except Exception:
            pass
    tmp = tempfile.mkdtemp(prefix="frames_", dir=cache_dir)
    try:
        start = a + step / 2.0
        r = subprocess.run([media.FFMPEG, "-v", "error", "-ss", "%.2f" % start, "-t", "%.2f" % max(1.0, b - start), "-i", source,
                            "-an", "-vf", "fps=1/%.4f,scale=320:-2" % step, "-q:v", "6", os.path.join(tmp, "f_%05d.jpg")],
                           capture_output=True, creationflags=media.NOWIN)
        if r.returncode != 0:
            raise IOError("Frame ထုတ်မရပါ: " + r.stderr.decode("utf-8", "replace")[-200:])
        out, seen = [], set()
        for i, fn in enumerate(sorted(os.listdir(tmp))):
            if cancelled():
                raise Stopped()
            data = open(os.path.join(tmp, fn), "rb").read()
            h = hashlib.md5(data).hexdigest()
            if h not in seen:                      # a still shot gives the same picture twice: keep one
                seen.add(h)
                out.append(("[%s]" % hms(start + i * step), data))
        pickle.dump(out, open(cache, "wb"))
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def make_speaker(cfg, voice, log, cancelled):
    """voice: 'edge:<name>' | 'google' | 'clone:<id>'.  Returns (speak(text) -> int16 @24k, signature, max piece length)."""
    kind, _, arg = voice.partition(":")
    if kind == "clone":
        steps, cs, best, soft = int(cfg["clone_steps"]), float(cfg["clone_cfg"]), int(cfg["clone_best"]), int(cfg["clone_soft"])
        if not cfg.get("own_url"):
            raise RuntimeError("Voice Clone GPU မချိတ်ရသေးပါ — Voice Clone page မှာ Colab ကို အရင်ချိတ်ပါ")
        return (lambda t: clone.speak(cfg, t, arg, steps, cs, best, soft, cancelled, log),
                "clone|%s|%d|%.1f|%d|%d" % (arg, steps, cs, best, soft), 220)
    if kind == "google":
        eng = GoogleTts(log, cancelled)
        return (lambda t: eng.synth(t, None, 0)), "google", 170
    eng = EdgeTts(log, cancelled)
    v = VOICES.get(arg, "my-MM-ThihaNeural")
    sp = int(cfg["speed_pct"])
    return (lambda t: eng.synth(t, v, sp)), "edge|%s|%d" % (v, sp), 300


# ---------------------------------------------------------------- prompts
ANALYSIS = """
You are a professional movie analyst and recap editor. Analyse this WHOLE movie from the first minute to the very ending (language: @@LANG@@). The kept part is @@KEPT@@ long.
You get (1) the complete time-coded dialogue and (2) frames from the whole movie, one about every @@STEP@@ seconds, each announced by its movie timestamp.
Understand the story as a whole - who the people are, where it happens, what drives the plot, the turning points, the climax and the ending - then find the key points of the story.

Return JSON only, exactly this shape:
{"title_mm": "catchy title in Burmese (Myanmar Unicode)",
 "logline": "English, 4-6 sentences: the complete story including the ending",
 "setting": "English: main places and time period",
 "characters": [{"name": "name as in the dialogue", "mm": "Burmese transliteration of the name", "role": "few English words"}],
 "key_points": [{"start": "H:MM:SS", "end": "H:MM:SS", "kind": "main", "importance": 5, "title": "short English title", "what": "English: what happens and why it matters"}]}

RULES:
- About @@POINTS@@ key_points, in story order from the beginning to the ending. Skip logos, credits and filler. The last key point is the ending.
- kind "main" (importance 4-5) = turning points, big events, the climax, the ending. kind "supporting" (importance 3) = events that explain the plot. kind "bridge" (importance 1-2) = short connecting moments, new places, transitions.
- "start"/"end" = where in the movie this is SEEN (20 to 90 seconds), using the dialogue timestamps and the frame timestamps. Never beyond @@KEEPTO@@.
- Do not invent events or names.

DIALOGUE:
@@TRANSCRIPT@@
"""

NARRATION = """
You are a Burmese movie-recap storyteller. The whole movie was already analysed; now write the narration for the next key points, in natural spoken Burmese (Myanmar Unicode, NOT Zawgyi).
You can SEE each point: the frames that follow are announced with their movie timestamps (they cover the points in the order below).

THE WHOLE STORY: @@LOGLINE@@
CHARACTERS (use exactly these Burmese names):
@@CHARS@@
NARRATION SO FAR (continue smoothly, never repeat it):
@@SPOKEN@@

KEY POINTS TO NARRATE:
@@DESC@@
Return JSON only, exactly this shape:
{"points": [{"id": 0, "lines": [{"text": "Burmese narration line", "start": "H:MM:SS", "end": "H:MM:SS"}]}]}

RULES:
- One entry per point above, same ids, in order. Each line is 1-2 spoken sentences; the characters of all its lines together stay inside the point's budget (it decides how long the voice speaks). More important points deserve richer telling; bridge points just a short line.
- "start"/"end" = the footage (inside that point's time range, 3 to 15 seconds) that shows what the line says. Use the frames to pick moments that really show it. Do not use the same footage twice.
- Warm storyteller voice of a popular movie-recap channel: short clear sentences ending with ။ , small pauses with ၊ . Name the characters, say what happens and why it matters. No English words, no emojis, no brackets, no quotation marks, numbers in Burmese words.
- MAKE IT GRIPPING, NOT A DRY REPORT: talk like a friend telling an exciting story. Show feelings, tension, surprise and small jokes where the scene allows; build suspense before a twist (a short line, then the reveal); sometimes ask the viewer a question; use vivid verbs and concrete details from the frames instead of generic words. Mix very short punchy sentences with longer flowing ones.
- VARY THE SENTENCE ENDINGS. Do NOT end sentence after sentence with the same word (especially never the plain "ပါတယ်" over and over). Use the natural range of spoken Burmese: plain "တယ်", past "ခဲ့တယ်", ongoing "နေတယ်", future / expectation "မယ်" / "လိမ့်မယ်", reported "တဲ့", emphasis "တော့တယ်" / "ပေါ့", story-teller flourishes, a sentence that ends on a noun or a question, etc. No ending may repeat more than twice in a row.
@@FIRSTLAST@@"""


def _fill(tpl, **kw):
    for k, v in kw.items():
        tpl = tpl.replace("@@%s@@" % k.upper(), str(v))
    return tpl.strip()


# ---------------------------------------------------------------- the run
def run_movie(cfg, req, log, stage, prog, cancelled, jobs_root, on_analysis=None):
    """req: movie, srt, lang, trim_start, trim_end, target_min, bg_pct, height, fx, voice, include (set | None), analyze_only.
    Returns the finished file ('' after an analysis-only run)."""
    if not media.check_ffmpeg():
        raise RuntimeError("ffmpeg မတွေ့ပါ — ffmpeg.exe ကို app နဲ့ folder တူတူ ထားပါ")
    keys = [k.strip() for k in cfg["keys"].split("\n") if k.strip()]
    if not keys:
        raise RuntimeError("Gemini API key မထည့်ရသေးပါ — ဆက်တင်မှာ ထည့်ပါ")
    t0 = time.time()
    source = req["movie"]
    info = media.probe(source)
    duration = info["duration"]
    if duration < 120:
        raise IOError("ဗီဒီယို အရှည် ၂ မိနစ်ထက် ရှည်ရမယ်")
    key = hashlib.sha1(("%s|%d|%d" % (os.path.abspath(source), os.path.getsize(source), int(os.path.getmtime(source)))).encode()).hexdigest()[:10]
    d = os.path.join(jobs_root, "movie_" + key)
    os.makedirs(d, exist_ok=True)
    keep_from = min(max(0.0, req["trim_start"]), duration - 60)
    keep_to = min(duration, req["trim_end"] if req["trim_end"] > keep_from + 60 else duration)
    kept = keep_to - keep_from
    target_sec = req["target_min"] * 60.0
    analyze_only = bool(req.get("analyze_only"))
    models = [m.strip() for m in cfg["models"].split(",") if m.strip()] or DEFAULT_MODELS
    ai = Gemini(keys, models, float(cfg["min_interval"]), log, cancelled)

    # 0. the voice must work before anything long starts; its speed sets the narration length budget
    cps, speak, vsig, piece_len = 11.0, None, "", 300
    if not analyze_only:
        stage("Voice စစ်ဆေး", 0.0)
        speak, vsig, piece_len = make_speaker(cfg, req["voice"], log, cancelled)
        probe = speak(SAMPLE)
        if len(probe) < RATE // 4:
            raise IOError("Voice အသံ မထွက်ပါ — Voice ရွေးချယ်မှု / Voice Clone ကို စစ်ပါ")
        cps = min(18.0, max(6.0, len(SAMPLE) / (len(probe) / float(RATE))))
        log("🎙 Voice အမြန်နှုန်း ~%.1f လုံး/စက္ကန့်" % cps)
    log("🎬 %s · ဖြတ်ပြီး %s · Recap %d မိနစ်" % (hms(duration), hms(kept), req["target_min"]))

    # 1. transcript of the kept part
    stage("မူရင်းအသံ နားထောင်", 0.07)
    if req.get("srt"):
        segs = [s for s in parse_srt(read_srt_text(req["srt"])) if keep_from - 1 <= s.start <= keep_to]
        if not segs:
            raise IOError("Subtitle ဖိုင်ထဲ စာ မတွေ့ပါ (.srt ဖြစ်ရမယ်)")
        log("📝 Subtitle %d ကြောင်း" % len(segs))
        lang_name = "as in the subtitles"
    else:
        wf = os.path.join(d, "words_%d_%d.json" % (keep_from, keep_to))
        if os.path.isfile(wf):
            log("♻️ Whisper ရလဒ် ရှိပြီးသား")
            words = [units.Word(**w) for w in json.load(open(wf, encoding="utf-8"))]
        else:
            audio = media.audio_16k(source, keep_from, kept)
            words = transcribe(audio, cfg["whisper"], LANGS.get(req["lang"], "en"), log, lambda f: prog(0.07 + 0.20 * f), cancelled)
            words = [units.Word(w.text, w.start + keep_from, w.end + keep_from) for w in words]
            json.dump([w.__dict__ for w in words], open(wf, "w", encoding="utf-8"), ensure_ascii=False)
            del audio
        segs = units.drop_loops(units.atomicize(words))
        if not segs:
            raise RuntimeError("စကားပြောသံ မတွေ့ပါ — Subtitle (.srt) ထည့်ကြည့်ပါ")
        lang_name = req["lang"]
    transcript = "\n".join("[%s] %s" % (hms(s.start), s.text) for s in segs)

    # 2. analyse the WHOLE movie
    stage("ဇာတ်ကား အစအဆုံး စိစစ်", 0.27)
    ana_file = os.path.join(d, "analysis_%d_%d_%s.json" % (keep_from, keep_to, hashlib.sha1(transcript.encode()).hexdigest()[:8]))
    if os.path.isfile(ana_file):
        log("♻️ စိစစ်ပြီးသား ရလဒ် ရှိတယ်")
        ana = json.load(open(ana_file, encoding="utf-8"))
    else:
        step = max(20.0, kept / 200.0)
        fr = extract_frames(source, keep_from, keep_to, step, d, cancelled)
        prompt = _fill(ANALYSIS, lang=lang_name, kept=hms(kept), step=int(step), points=min(90, max(12, int(kept / 60 / 3))),
                       keepto=hms(keep_to), transcript=transcript)
        ana = None
        for _ in range(2):
            o = ai.json(prompt, frames=fr)
            if isinstance(o, dict) and isinstance(o.get("key_points"), list) and len(o["key_points"]) >= 4:
                ana = o
                break
        if ana is None:
            raise RuntimeError("ဇာတ်ကား စိစစ်တာ မအောင်မြင်ပါ — ပြန်စမ်းပါ")
        json.dump(ana, open(ana_file, "w", encoding="utf-8"), ensure_ascii=False)
    title, logline = str(ana.get("title_mm", "")), str(ana.get("logline", ""))
    chars = "\n".join("%s = %s (%s)" % (c.get("name", ""), c.get("mm", ""), c.get("role", "")) for c in (ana.get("characters") or []) if isinstance(c, dict))
    pts = []
    for o in ana["key_points"]:
        if not isinstance(o, dict):
            continue
        st = min(max(parse_time(o.get("start")), keep_from), keep_to - 5)
        en = parse_time(o.get("end"))
        if en < st + 5:
            en = st + 20
        try:
            imp = min(5, max(1, int(o.get("importance", 3))))
        except (TypeError, ValueError):
            imp = 3
        pts.append({"id": len(pts), "start": st, "end": min(en, keep_to), "kind": str(o.get("kind", "supporting")), "imp": imp,
                    "title": str(o.get("title", "")), "what": str(o.get("what", ""))})
    if len(pts) < 4:
        raise RuntimeError("Key point %d ခုပဲ ရတယ် — ပြန်စမ်းပါ" % len(pts))
    sel = select([p["imp"] for p in pts], target_sec, req.get("include"))
    secs = seconds([p["imp"] for p in pts], sel, target_sec)
    log("🧩 %d key point (main %d) · %d ခုကို Recap လုပ်မယ်" % (len(pts), sum(1 for p in pts if p["imp"] >= 4), sum(sel)))
    if on_analysis:
        on_analysis({"title": title, "logline": logline, "target_sec": target_sec,
                     "points": [dict(p, selected=sel[i], seconds=secs[i]) for i, p in enumerate(pts)]})
    if analyze_only:
        prog(1.0)
        return ""

    # 3. the chosen points, in story order: AI looks closely -> narration -> voice -> picture
    chosen = [i for i in range(len(pts)) if sel[i]]
    tts_dir, ren_dir = os.path.join(d, "tts"), os.path.join(d, "render")
    os.makedirs(tts_dir, exist_ok=True)
    os.makedirs(ren_dir, exist_ok=True)
    fps = int(round(info["fps"]))
    h = min(int(req["height"]), info["height"])
    h -= h % 2
    pause_pct = 100
    tail = RATE * 6 // 100
    spoken, all_lines, fx_cut, done_pts = [], [], 0, 0
    base0, span = 0.33, 0.57
    workers = max(1, int(cfg["workers"]))

    bi = 0
    while bi < len(chosen):
        if cancelled():
            raise Stopped()
        batch = chosen[bi:bi + 3]
        stage("Point %d/%d · AI စေ့စေ့ကြည့်" % (bi + 1, len(chosen)), base0 + span * done_pts / max(1, len(chosen)))

        sig = ",".join("%d:%d" % (pts[i]["id"], int(secs[i])) for i in batch)
        zf = os.path.join(d, "zoom_%s.json" % hashlib.sha1(("%s|%d|%s" % (os.path.basename(ana_file), int(target_sec), sig)).encode()).hexdigest()[:10])
        if os.path.isfile(zf):
            zoom = json.load(open(zf, encoding="utf-8"))
        else:
            fr, desc = [], []
            for i in batch:
                p = pts[i]
                w0, w1 = max(keep_from, p["start"] - 3), min(keep_to, p["end"] + 3)
                fr += extract_frames(source, w0, w1, max(2.5, (w1 - w0) / 30.0), d, cancelled)
                prev_chosen = chosen[chosen.index(i) - 1] if chosen.index(i) > 0 else -1
                skipped = " ".join("• " + pts[j]["what"] for j in range(i) if not sel[j] and j > prev_chosen)
                dialog = " ".join(s.text for s in segs if w0 <= s.start <= w1)[:700]
                budget = int(secs[i] * cps * 0.95)
                n_lines = min(10, max(1, int(round(secs[i] / 7.0))))
                desc.append("POINT id=%d [%s, importance %d] %s to %s\n  WHAT HAPPENS: %s\n  NARRATION: about %d seconds = about %d Burmese characters (min %d, max %d), about %d lines\n" % (
                    p["id"], p["kind"], p["imp"], hms(w0), hms(w1), p["what"], int(secs[i]), budget, int(budget * 0.85), int(budget * 1.15), n_lines)
                    + ("  LEFT OUT BEFORE THIS POINT (you may bridge it with one short clause): %s\n" % skipped if skipped.strip() else "")
                    + "  DIALOGUE: %s\n\n" % dialog)
            first, last = batch[0] == chosen[0], batch[-1] == chosen[-1]
            fl = ("- The very first line must hook the viewer right away.\n" if first else "") + ("- The very last line must close the story with the ending.\n" if last else "")
            prompt = _fill(NARRATION, logline=logline, chars=chars, spoken="\n".join(spoken[-3:]) or "(this is the start of the recap)",
                           desc="".join(desc), firstlast=fl)
            got, monotone, extra = None, None, ""
            for attempt in range(3):
                o = ai.json(prompt + extra, frames=fr)
                a = o.get("points") if isinstance(o, dict) else None
                if not isinstance(a, list):
                    continue
                ids = [x.get("id") for x in a if isinstance(x, dict)]
                if all(pts[i]["id"] in ids for i in batch):
                    if attempt < 2 and endings_too_same(a):
                        monotone = o
                        extra = "\n- YOUR LAST DRAFT ENDED TOO MANY SENTENCES WITH THE SAME WORD. Rewrite it with clearly varied endings and a livelier, more gripping storyteller tone."
                        continue
                    got = o
                    break
            got = got or monotone
            if got is None:
                raise RuntimeError("AI ရေးတာ မပြည့်စုံပါ (Point %d) — ပြန်စမ်းပါ" % (bi + 1))
            json.dump(got, open(zf, "w", encoding="utf-8"), ensure_ascii=False)
            zoom = got
        by_id = {x.get("id"): x.get("lines") or [] for x in zoom["points"] if isinstance(x, dict)}

        for i in batch:
            if cancelled():
                raise Stopped()
            p = pts[i]
            lines = []
            for o in by_id.get(p["id"], [])[:14]:
                if not isinstance(o, dict):
                    continue
                text = str(o.get("text", "")).strip()
                if len(text) < 4 or not any(ch.isalnum() for ch in text):
                    continue
                st = min(max(parse_time(o.get("start")), keep_from), keep_to - 2)
                en = parse_time(o.get("end"))
                if en < st + 2:
                    en = st + 4
                lines.append({"text": text, "start": st, "end": min(en, keep_to)})
            if not lines:
                raise RuntimeError("Point %d — narration မရှိပါ" % p["id"])
            spoken += [l["text"] for l in lines]

            # voice
            stage("Point %d/%d · အသံထုတ်" % (done_pts + 1, len(chosen)), base0 + span * (done_pts + 0.3) / len(chosen))
            clips = []
            for li, ln in enumerate(lines):
                if cancelled():
                    raise Stopped()
                f = os.path.join(tts_dir, "pt%03d_l%02d_%s.npy" % (p["id"], li, hashlib.sha1(("v3|%s|%d|%s" % (vsig, pause_pct, ln["text"])).encode()).hexdigest()[:10]))
                if os.path.isfile(f):
                    pcm = np.load(f)
                else:
                    pieces = split_text(ln["text"], piece_len)
                    parts = []
                    for pi, (ptxt, _) in enumerate(pieces):
                        raw = speak(ptxt)
                        if len(raw) < RATE // 20:
                            raise IOError("Voice အသံ တိုလွန်းနေတယ် (Point %d)" % p["id"])
                        parts += [polish_speech(raw), np.zeros(RATE * (170 if pi == len(pieces) - 1 else 110) * pause_pct // 100 // 1000, np.int16)]
                    parts.append(np.zeros(tail, np.int16))
                    pcm = np.concatenate(parts)
                    np.save(f + ".tmp.npy", pcm)
                    os.replace(f + ".tmp.npy", f)
                clips.append(pcm)

            # picture of every line: footage as long as its voice (slowed a little when the scene is short)
            stage("Point %d/%d · ရုပ်ဖြတ်တပ်" % (done_pts + 1, len(chosen)), base0 + span * (done_pts + 0.6) / len(chosen))
            jobs = []
            for li, ln in enumerate(lines):
                dd = len(clips[li]) / float(RATE)
                rate = min(1.25, max(0.5, (ln["end"] - ln["start"]) / dd))
                s0 = ln["start"]
                if s0 + dd * rate > keep_to + 5:
                    s0 = max(keep_from, keep_to + 5 - dd * rate)
                s0 = min(max(0.0, s0), max(0.0, duration - dd * rate - 0.3))
                fx = bool(req.get("fx", True))
                z_in = fx_cut % 2 == 0
                fx_cut += 1
                za, zb = ((1.0, 1.06) if z_in else (1.06, 1.0)) if fx else (1.0, 1.0)
                fdin = min(0.22, dd / 4) if fx and li == 0 else 0.0
                fdout = min(0.22, dd / 4) if fx and li == len(lines) - 1 else 0.0
                cf = os.path.join(ren_dir, "pt%03d_l%02d_%s.mp4" % (p["id"], li, hashlib.sha1(
                    ("fx3|%s|%s|%.2f|%.3f|%.3f|%d|%s" % (fx, z_in, s0, rate, dd, h, ln["text"])).encode()).hexdigest()[:10]))
                jobs.append((cf, (0.0, dd, s0, rate, za, zb, fdin, fdout)))
                all_lines.append({"pcm": clips[li], "clip": cf, "src": s0, "text": ln["text"]})

            def render(j):
                if not (os.path.isfile(j[0]) and os.path.getsize(j[0]) > 0):
                    if cancelled():
                        raise Stopped()
                    media.render_segment(source, j[0], j[1], info, fps, h)
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(render, jobs))
            done_pts += 1
            total_sec = sum(len(l["pcm"]) for l in all_lines) / float(RATE)
            log("✅ Point %d/%d — စုစုပေါင်း %.1f / %d မိနစ်" % (done_pts, len(chosen), total_sec / 60, req["target_min"]))
            prog(base0 + span * done_pts / len(chosen))
        bi += len(batch)

    # 4. join + narration track (+ quiet original sound) + export
    stage("ပေါင်းစပ်", 0.90)
    video_all = os.path.join(d, "video_all.mp4")
    media.concat_copy([l["clip"] for l in all_lines], video_all, d)
    prog(0.94)
    gain = int(req["bg_pct"]) / 100.0
    bg = None
    bg_file = os.path.join(d, "audio16k.pcm")
    if gain > 0:
        r = subprocess.run([media.FFMPEG, "-y", "-v", "error", "-i", source, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", bg_file],
                           capture_output=True, creationflags=media.NOWIN)
        if r.returncode == 0 and os.path.getsize(bg_file) > 0:
            bg = np.memmap(bg_file, dtype="<i2", mode="r")
    track, subs, t = [], [], 0.0
    for l in all_lines:
        pcm = l["pcm"].astype(np.float32)
        if bg is not None:
            a = int(l["src"] * 16000)
            n_src = int(ceil(len(pcm) * 16000.0 / RATE)) + 2
            seg = np.zeros(n_src, np.float32)
            chunk = np.asarray(bg[a:a + n_src], dtype=np.float32)
            seg[:len(chunk)] = chunk
            pos = np.arange(len(pcm)) * 16000.0 / RATE
            pcm = pcm + np.interp(pos, np.arange(n_src), seg) * gain
        track.append(np.clip(pcm, -32768, 32767).astype(np.int16))
        dd = len(l["pcm"]) / float(RATE)
        subs.append((t, t + max(0.5, dd - 0.25), l["text"]))
        t += dd
    wav = os.path.join(d, "narration.wav")
    media.write_wav(wav, np.concatenate(track), RATE)
    prog(0.96)
    base = safe_name(os.path.splitext(os.path.basename(source))[0], "movie", 50)
    mins = int(round(t / 60.0))
    out_dir = req["out_dir"]
    os.makedirs(out_dir, exist_ok=True)
    final = os.path.join(out_dir, "%s_recap_%dm.mp4" % (base, mins))
    media.mux(video_all, wav, final)
    with open(os.path.join(out_dir, "%s_recap_%dm.srt" % (base, mins)), "w", encoding="utf-8") as f:
        for n, (a, b, text) in enumerate(subs, 1):
            f.write("%d\n%s --> %s\n%s\n\n" % (n, units.fmt_ts(a), units.fmt_ts(b), text))
    del bg
    for p in (video_all, wav, bg_file):
        try:
            os.remove(p)
        except OSError:
            pass
    shutil.rmtree(tts_dir, ignore_errors=True)
    shutil.rmtree(ren_dir, ignore_errors=True)
    prog(1.0)
    log("✅ ပြီးပါပြီ — %.1f မိနစ် (%d မိနစ် ကြာ) — %s" % (t / 60, (time.time() - t0) / 60, final))
    return final
