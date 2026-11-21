"""The other tools: SRT, TTS, Splitter, Dhamma Studio. Same contract as Pipeline: (cfg, args…, log, stage, prog, cancelled) -> path."""
import hashlib
import json
import os
import random
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import media, units
from .gemini import DEFAULT_MODELS, Gemini, Stopped
from .pipeline import LANGS, job_dir, transcribe
from .translator import Translator
from .tts import EdgeTts, GoogleTts, RATE, VOICES


def _safe(name, default="file"):
    return re.sub(r"[\\/:*?\"<>|]+", "_", name).strip()[:60] or default


def _keys(cfg):
    ks = [k.strip() for k in cfg["keys"].split("\n") if k.strip()]
    if not ks:
        raise RuntimeError("Gemini API key မထည့်ရသေးပါ — ဆက်တင်မှာ ထည့်ပါ")
    return ks


def _need_ffmpeg():
    if not media.check_ffmpeg():
        raise RuntimeError("ffmpeg မတွေ့ပါ — ffmpeg.exe ကို app နဲ့ folder တူတူ ထားပါ")


# ================================================================== SRT
def run_srt(cfg, source, translate, out_dir, log, stage, prog, cancelled, jobs_root):
    """Video or audio in, subtitle (.srt) out — no dubbing, no new video."""
    _need_ffmpeg()
    keys = _keys(cfg) if translate else []
    lang = LANGS.get(cfg["lang"], "en")
    work = job_dir(jobs_root, source)
    base = _safe(os.path.splitext(os.path.basename(source))[0], "subtitle")

    stage("အသံထုတ်ယူ", 0.02)
    audio = media.audio_16k(source)
    dur = len(audio) / 16000.0
    prog(0.10)
    stage("နားထောင်နေတယ် (Whisper)", 0.10)
    wf = os.path.join(work, "words.json")
    if os.path.isfile(wf):
        words = [units.Word(**w) for w in json.load(open(wf, encoding="utf-8"))]
    else:
        words = transcribe(audio, cfg["whisper"], lang, log, lambda f: prog(0.10 + f * (0.45 if translate else 0.85)), cancelled)
        json.dump([w.__dict__ for w in words], open(wf, "w", encoding="utf-8"), ensure_ascii=False)
    segs = units.drop_loops(units.atomicize(words))
    if not segs:
        raise RuntimeError("%s စကားပြောသံ မတွေ့ပါ" % cfg["lang"])
    units.assign_coverage(segs, dur)

    if translate:
        models = [m.strip() for m in cfg["models"].split(",") if m.strip()] or DEFAULT_MODELS
        ai = Gemini(keys, models, float(cfg["min_interval"]), log, cancelled)
        tr = Translator(ai, log, cancelled, cfg["lang"], cfg["glossary"])
        stage("ဇာတ်လမ်း မှတ်ဉာဏ်", 0.55)
        mem = tr.story_context(segs, os.path.join(work, "story.json"), lambda f: prog(0.55 + f * 0.05))
        stage("မြန်မာလို ဘာသာပြန်", 0.60)
        tr.translate_all(segs, mem, os.path.join(work, "translations.json"), lambda f: prog(0.60 + f * 0.38))

    stage("SRT ရေးနေတယ်", 0.99)
    lines, n = [], 0
    for s in segs:
        t = (s.translation if translate else s.text).strip()
        if not t:
            continue
        n += 1
        end = max(s.end, s.start + 0.6)
        lines.append("%d\n%s --> %s\n%s\n" % (n, units.fmt_ts(s.start), units.fmt_ts(end), t))
    if n == 0:
        raise RuntimeError("စာတန်း တစ်ကြောင်းမှ မထွက်ပါ")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "%s_%s.srt" % (base, "mm" if translate else (lang or "auto")))
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    prog(1.0)
    log("✅ စာတန်း %d ကြောင်း — %s" % (n, out))
    return out


# ================================================================== TTS
def split_text(text, mx=300):
    """Text pieces of at most [mx] characters, each with the silence (ms) to leave after it."""
    out = []
    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        parts, cur = [], ""
        for s in [p for p in re.split(r"(?<=[။.!?？！])\s*", line) if p.strip()]:
            rest = s
            while len(rest) > mx:
                if cur:
                    parts.append(cur)
                    cur = ""
                parts.append(rest[:mx])
                rest = rest[mx:]
            if len(cur) + len(rest) + 1 > mx and cur:
                parts.append(cur)
                cur = ""
            cur = (cur + " " + rest) if cur else rest
        if cur:
            parts.append(cur)
        parts = [p for p in parts if any(ch.isalnum() for ch in p)]       # '။။' / '♪' alone cannot be spoken
        for i, p in enumerate(parts):
            out.append((p, 650 if i == len(parts) - 1 else 300))
    return out


def make_engine(cfg, log, cancelled):
    return EdgeTts(log, cancelled) if cfg["engine"] == "edge" else GoogleTts(log, cancelled)


def run_tts(cfg, text, out_dir, log, stage, prog, cancelled, jobs_root):
    """Burmese text in, one audio file (.mp3) out — no video, no translation."""
    _need_ffmpeg()
    pieces = split_text(text)
    if not pieces:
        raise ValueError("စာ ရိုက်ထည့်ပါ")
    eng = make_engine(cfg, log, cancelled)
    voice = VOICES.get(cfg["voice"], "my-MM-ThihaNeural")
    speed = int(cfg["speed_pct"]) if cfg["engine"] == "edge" else 0
    key = hashlib.sha1(("%s|%s|%s|%d" % (text, cfg["engine"], voice, speed)).encode()).hexdigest()[:16]
    cache = os.path.join(jobs_root, "tts_" + key)           # resume: finished pieces stay on disk
    os.makedirs(cache, exist_ok=True)
    stage("အသံ ထုတ်နေတယ်", 0.0)
    chunks = []
    for i, (t, gap) in enumerate(pieces):
        if cancelled():
            raise Stopped()
        f = os.path.join(cache, "%05d.npy" % i)
        if os.path.isfile(f):
            pcm = np.load(f)
        else:
            pcm = eng.synth(t, voice, speed)
            if len(pcm) < RATE // 20:
                raise IOError("TTS အသံ တိုလွန်းနေတယ် (အပိုင်း %d)" % (i + 1))
            np.save(f + ".tmp.npy", pcm)
            os.replace(f + ".tmp.npy", f)
        chunks.append(pcm)
        chunks.append(np.zeros(RATE * gap // 1000, dtype=np.int16))
        log("🔊 အသံ %d/%d" % (i + 1, len(pieces)))
        prog((i + 1) / len(pieces) * 0.95)
    wav = os.path.join(cache, "all.wav")
    media.write_wav(wav, np.concatenate(chunks), RATE)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "tts_%d.mp3" % int(time.time()))
    stage("ဖိုင် ပြုလုပ်နေတယ်", 0.95)
    media.encode_audio(wav, out)
    shutil.rmtree(cache, ignore_errors=True)
    prog(1.0)
    log("✅ " + out)
    return out


# ================================================================== Splitter
def split_boundaries(keys, duration, part_sec):
    """Start times of each part: key frames closest after every k * part_sec; a tail shorter than 3 s joins the previous part."""
    if not keys:
        raise IOError("ဒီဗီဒီယိုမှာ key frame မတွေ့ပါ")
    last = duration
    out = [keys[0]]
    k = 1
    while True:
        target = keys[0] + k * part_sec
        if target >= last - 3.0:
            break
        s = next((x for x in keys if x >= target), None)
        if s is None:
            break
        if s > out[-1]:
            out.append(s)
        k += 1
    return out


def run_split(cfg, source, part_min, start_ep, out_dir, log, stage, prog, cancelled, jobs_root):
    """Cuts a finished video into parts WITHOUT re-encoding (no quality loss, takes seconds). Cuts land on key frames."""
    _need_ffmpeg()
    info = media.probe(source)
    stage("ဖြတ်မယ့်နေရာတွေ ရှာနေတယ်", 0.02)
    starts = split_boundaries(media.keyframes(source), info["duration"], part_min * 60)
    if len(starts) < 2:
        raise RuntimeError("ဗီဒီယိုက %d မိနစ်ထက် တိုနေလို့ ခွဲစရာမလိုပါ" % part_min)
    base = _safe(os.path.splitext(os.path.basename(source))[0], "video")
    dest = os.path.join(out_dir, "Split")
    os.makedirs(dest, exist_ok=True)
    outs = []
    for p, st in enumerate(starts):
        if cancelled():
            raise Stopped()
        end = starts[p + 1] if p + 1 < len(starts) else info["duration"] + 1
        out = os.path.join(dest, "%s_EP%02d.mp4" % (base, start_ep + p))
        stage("အပိုင်း %d/%d သိမ်းနေတယ်" % (p + 1, len(starts)), (p + 0.1) / len(starts))
        media.run_ff([media.FFMPEG, "-y", "-v", "error", "-ss", "%.3f" % st, "-i", source, "-t", "%.3f" % (end - st),
                      "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", out], "ဖြတ်ခြင်း")
        outs.append(out)
        prog((p + 1) / len(starts))
        log("✂️ %s" % os.path.basename(out))
    log("✅ အပိုင်း %d ခု — %s" % (len(outs), dest))
    return dest


# ================================================================== Dhamma Studio
LONG, PIECE, FPS, DRATE = 15.0, 10.0, 30, 44100


def canvas(ratio, short):
    """ratio: 0 = 9:16, 1 = 16:9, 2 = 1:1; short = short side in px."""
    ev = lambda v: max(2, int(round(v / 2.0)) * 2)
    if ratio == 1:
        return ev(short * 16 / 9.0), short
    if ratio == 2:
        return short, short
    return short, ev(short * 16 / 9.0)


def dhamma_plan(clips, audio_sec, rnd=None):
    """clips: [(path, dur)]. Whole clips are the units (long ones are cut into ~10 s pieces); a new random pass starts only when all units are used."""
    rnd = rnd or random.Random()
    units_ = []
    for i, (_, d) in enumerate(clips):
        if d < 0.5:
            continue
        if d <= LONG:
            units_.append((i, 0.0, d))
        else:
            n = max(2, int(round(d / PIECE)))
            ln = d / n
            units_ += [(i, k * ln, ln) for k in range(n)]
    if not units_:
        raise IOError("သုံးလို့ရတဲ့ video မရှိပါ")
    out, total, last = [], 0.0, -1
    while total < audio_sec - 0.001:
        pas = units_[:]
        rnd.shuffle(pas)
        for i in range(len(pas)):                       # never the same clip twice in a row when another one can go between
            prev = last if i == 0 else pas[i - 1][0]
            if pas[i][0] == prev:
                j = next((x for x in range(i + 1, len(pas)) if pas[x][0] != prev), None)
                if j is not None:
                    pas[i], pas[j] = pas[j], pas[i]
        for c, s, d in pas:
            left = audio_sec - total
            if left <= 0.001:
                break
            dd = min(d, left)
            out.append((c, s, dd))
            total += dd
            last = c
    return out


class Branding:
    def __init__(self, title="", subtitle="", title_mode=0, intro=False, outro=False, outro_text="", logo="", logo_right=True):
        self.title, self.subtitle, self.title_mode = title.strip(), subtitle.strip(), title_mode
        self.intro, self.outro, self.outro_text = intro, outro, outro_text.strip()
        self.logo, self.logo_right = logo, logo_right

    @property
    def intro_sec(self):
        return 3.0 if self.intro and self.title else 0.0

    @property
    def outro_sec(self):
        return 4.0 if self.outro and self.outro_text else 0.0


class Ticker:
    def __init__(self, text, speed=0.12, size=0.07, color="#FFFFFF", lift=0.0):
        self.text, self.speed, self.size, self.color, self.lift = text.strip(), speed, size, color, lift


def run_dhamma(cfg, clip_paths, audio_path, ratio, short, ticker, brand, out_dir, log, stage, prog, cancelled, jobs_root, textimg=None):
    """Many clips + one audio file -> one video as long as the audio. Original sound of the clips is dropped."""
    _need_ffmpeg()
    if textimg is None:
        from . import textimg
    if not clip_paths:
        raise RuntimeError("Video clip တစ်ခုမှ မရွေးရသေးပါ")
    if not audio_path:
        raise RuntimeError("Audio ဖိုင် မရွေးရသေးပါ")
    work = os.path.join(jobs_root, "dhamma_%d" % int(time.time()))
    os.makedirs(work, exist_ok=True)
    try:
        stage("Audio ပြင်နေတယ်", 0.0)
        pcm = media.audio_pcm(audio_path, DRATE)
        audio_sec = len(pcm) / DRATE
        if audio_sec < 0.5:
            raise IOError("Audio အရမ်းတိုနေတယ် / ဖတ်မရပါ")
        parts = [np.zeros(int(DRATE * brand.intro_sec), np.int16), pcm, np.zeros(int(DRATE * brand.outro_sec), np.int16)]
        wav = os.path.join(work, "audio.wav")
        media.write_wav(wav, np.concatenate(parts), DRATE)
        total = audio_sec + brand.intro_sec + brand.outro_sec
        prog(0.04)

        stage("Video စီနေတယ်", 0.05)
        clips = []
        for p in clip_paths:
            try:
                clips.append((p, media.probe(p)["duration"]))
            except IOError as e:
                log("⚠️ ကျော်ထားတယ်: %s" % os.path.basename(p))
        plan = dhamma_plan(clips, audio_sec)
        W, H = canvas(ratio, short)
        log("🎞 %d အပိုင်း · %dx%d · %.1f မိနစ်" % (len(plan), W, H, total / 60))

        vf = "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,fps=%d,setsar=1,format=yuv420p" % (W, H, W, H, FPS)
        done = [0]

        def one(i):
            c, st, d = plan[i]
            out = os.path.join(work, "p_%05d.mp4" % i)
            if cancelled():
                raise Stopped()
            media.run_ff([media.FFMPEG, "-y", "-v", "error", "-ss", "%.3f" % st, "-t", "%.3f" % d, "-i", clips[c][0], "-an",
                          "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", str(FPS), out], "Video အပိုင်း")
            done[0] += 1
            prog(0.05 + 0.55 * done[0] / len(plan))
            return out

        with ThreadPoolExecutor(max_workers=max(1, int(cfg["workers"]))) as ex:
            body = list(ex.map(one, range(len(plan))))

        def card(name, parts_, sec):
            png = os.path.join(work, name + ".png")
            textimg.card_png(png, W, H, parts_)
            out = os.path.join(work, name + ".mp4")
            media.run_ff([media.FFMPEG, "-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-t", "%.2f" % sec, "-i", png,
                          "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-r", str(FPS), out], "Card")
            return out

        short_side = min(W, H)
        seq = []
        if brand.intro_sec:
            seq.append(card("intro", [(brand.title, 0.07, "#FFFFFF"), (brand.subtitle, 0.04, "#CFCFFF")] if brand.subtitle
                            else [(brand.title, 0.07, "#FFFFFF")], brand.intro_sec))
        seq += body
        if brand.outro_sec:
            seq.append(card("outro", [(brand.outro_text, 0.055, "#FFFFFF")], brand.outro_sec))
        lst = os.path.join(work, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for p in seq:
                f.write("file '%s'\n" % p.replace("\\", "/").replace("'", "'\\''"))
        joined = os.path.join(work, "joined.mp4")
        media.run_ff([media.FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined], "ပေါင်းခြင်း")
        prog(0.62)

        # overlays: title, logo, running text — one pass over the joined picture
        inputs, chains, last = ["-i", joined], [], "[0:v]"
        n = 1
        if brand.title and brand.title_mode in (1, 2):
            png = os.path.join(work, "title.png")
            tw, th = textimg.title_png(png, W, H, brand.title, brand.subtitle)
            inputs += ["-loop", "1", "-i", png]
            en = "gte(t,%.2f)" % brand.intro_sec if brand.title_mode == 2 else "between(t,%.2f,%.2f)" % (brand.intro_sec, brand.intro_sec + 8)
            if brand.title_mode == 2:
                en = "between(t,%.2f,%.2f)" % (brand.intro_sec, total - brand.outro_sec)
            chains.append("%s[%d:v]overlay=x=(W-w)/2:y=%d:enable='%s'[v%d]" % (last, n, int(H * 0.05), en, n))
            last, n = "[v%d]" % n, n + 1
        if brand.logo and os.path.isfile(brand.logo):
            m = int(short_side * 0.03)
            inputs += ["-i", brand.logo]
            chains.append("[%d:v]scale=-2:%d[lg]" % (n, int(short_side * 0.10)))
            x = "W-w-%d" % m if brand.logo_right else str(m)
            chains.append("%s[lg]overlay=x=%s:y=%d[v%d]" % (last, x, m, n))
            last, n = "[v%d]" % n, n + 1
        if ticker and ticker.text:
            png = os.path.join(work, "ticker.png")
            sw, bar_h = textimg.ticker_png(png, W, H, ticker.text, ticker.size, ticker.color)
            speed_px = ticker.speed * short_side
            y = H - int(H * ticker.lift) - bar_h
            inputs += ["-loop", "1", "-i", png]
            chains.append("%sdrawbox=x=0:y=%d:w=iw:h=%d:color=black@0.7:t=fill[b%d]" % (last, y, bar_h, n))
            chains.append("[b%d][%d:v]overlay=x='W-mod(t*%.3f,%d+W)':y=%d[v%d]" % (n, n, speed_px, sw, y, n))
            last, n = "[v%d]" % n, n + 1
        final = os.path.join(work, "final.mp4")
        stage("ရေးစာတွေ ထည့်နေတယ်", 0.62)
        if chains:
            media.run_ff([media.FFMPEG, "-y", "-v", "error"] + inputs + ["-i", wav, "-filter_complex", ";".join(chains),
                          "-map", last, "-map", "%d:a" % n, "-t", "%.3f" % total, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", final], "Overlay")
        else:
            media.run_ff([media.FFMPEG, "-y", "-v", "error", "-i", joined, "-i", wav, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                          "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", final], "ပေါင်းခြင်း")
        os.makedirs(os.path.join(out_dir, "Dhamma"), exist_ok=True)
        out = os.path.join(out_dir, "Dhamma", "Dhamma_%s.mp4" % time.strftime("%Y%m%d_%H%M%S"))
        shutil.copyfile(final, out)
        prog(1.0)
        log("✅ " + out)
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)
