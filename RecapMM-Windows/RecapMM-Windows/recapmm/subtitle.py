"""Subtitle Only: many videos (a whole folder of episodes) -> each one comes back as its own file with Burmese subtitles burned in,
shown only while the people speak, over a blur that hides the original subtitle. The original sound is kept untouched.
Per video: Whisper listens -> Gemini translates (story memory carried from episode to episode) -> subtitle images -> ffmpeg."""
import json
import os
import re
import shutil
import time

from . import media, units
from .gemini import DEFAULT_MODELS, Gemini, KeyPoolExhausted, Stopped
from .pipeline import LANGS, job_dir, transcribe
from .translator import Translator

VIDEO_EXT = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv", ".ts", ".wmv")


def natural_key(path):
    """EP2 before EP10."""
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", path)]


def scan_folder(folder):
    out = []
    for root, _, files in os.walk(folder):
        out += [os.path.join(root, f) for f in files if f.lower().endswith(VIDEO_EXT)]
    return sorted(out, key=natural_key)


def collect(paths):
    """Files and folders (any mix) -> a de-duplicated, naturally sorted list of videos."""
    seen, out = set(), []
    for p in paths:
        for f in (scan_folder(p) if os.path.isdir(p) else [p] if (p.lower().endswith(VIDEO_EXT) and os.path.isfile(p)) else []):
            k = os.path.normcase(os.path.abspath(f))
            if k not in seen:
                seen.add(k)
                out.append(f)
    return sorted(out, key=natural_key)


class SubOpts:
    def __init__(self, burn=True, blur=True, blur_always=False, bottom_pct=5.0, band_pct=16.0, size_pct=5.0,
                 color="#FFFFFF", box=True, skip_done=True):
        self.burn, self.blur, self.blur_always = burn, blur, blur_always
        self.bottom_pct, self.band_pct, self.size_pct = bottom_pct, band_pct, size_pct
        self.color, self.box, self.skip_done = color, box, skip_done


def make_cues(segs):
    """(start, end, text): visible only while the people speak. Never overlaps the next line, never flashes for less than 0.9 s."""
    cues = []
    for i, s in enumerate(segs):
        text = s.translation.strip()
        if not text:
            continue
        start = s.start
        end = max(s.end + 0.10, start + 0.9)
        if i + 1 < len(segs):
            end = min(end, max(start + 0.3, segs[i + 1].start - 0.02))
        cues.append((start, end, text))
    return cues


def geometry(info, opts):
    H = info["height"] - info["height"] % 2
    W = info["width"] - info["width"] % 2
    band = max(40, int(round(H * opts.band_pct / 100.0 / 2)) * 2)
    y = H - band - int(round(H * opts.bottom_pct / 100.0))
    y = max(0, y - y % 2)
    return W, H, band, min(y, H - band)


def _fmt(x):
    return "%.3f" % x


def build_filter(W, band, y, cues, opts):
    """Filter graph text: blur the band (when asked), then lay the subtitle strip video over it."""
    parts, cur = [], "[0:v]"
    if opts.blur:
        sigma = max(6, band // 4)
        en = ""
        if not opts.blur_always:
            en = ":enable='%s'" % "+".join("between(t,%s,%s)" % (_fmt(a - 0.05), _fmt(b + 0.05)) for a, b, _ in cues)
        parts.append("%ssplit=2[base][tmp]" % cur)
        parts.append("[tmp]crop=%d:%d:0:%d,gblur=sigma=%d:steps=2[bl]" % (W, band, y, sigma))
        parts.append("[base][bl]overlay=0:%d%s[v1]" % (y, en))
        cur = "[v1]"
    parts.append("%s[1:v]overlay=0:%d:eof_action=pass,format=yuv420p[v]" % (cur, y))
    return ";\n".join(parts)


def write_concat(path, blank, items, total):
    """Concat-demuxer list: the strip video = blank / cue image / blank ... for exactly the length of the movie."""
    lines, t = [], 0.0

    def add(png, dur):
        lines.append("file '%s'" % png.replace("\\", "/").replace("'", "'\\''"))
        lines.append("duration %.3f" % max(0.04, dur))
    last = blank
    for a, b, png in items:
        if a > t + 0.01:
            add(blank, a - t)
        add(png, b - max(a, t))
        t, last = b, png
    if total > t:
        add(blank, total - t)
        last = blank
    lines.append("file '%s'" % last.replace("\\", "/").replace("'", "'\\''"))      # concat quirk: the last file needs repeating
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")


def process_one(cfg, source, opts, out_dir, prev_memory, log, stage, prog, cancelled, jobs_root, textimg=None, ai=None):
    """One video -> (output path or '', story memory). prog gets 0..1 for this video."""
    if textimg is None and opts.burn:
        from . import textimg
    info = media.probe(source)
    dur = info["duration"]
    work = job_dir(jobs_root, source)
    base = re.sub(r"[\\/:*?\"<>|]+", "_", os.path.splitext(os.path.basename(source))[0]).strip()[:80] or "video"
    os.makedirs(out_dir, exist_ok=True)
    final = os.path.join(out_dir, base + ("_sub.mp4" if opts.burn else "_mm.srt"))
    if opts.skip_done and os.path.isfile(final) and os.path.getsize(final) > 0:
        log("⏭ ပြီးသားမို့ ကျော်တယ်: %s" % os.path.basename(final))
        mem_f = os.path.join(work, "story.json")
        mem = prev_memory
        if os.path.isfile(mem_f):
            try:
                mem = json.load(open(mem_f, encoding="utf-8")).get("context") or prev_memory
            except Exception:
                pass
        return final, mem

    stage("အသံထုတ်ယူ")
    audio = media.audio_16k(source)
    prog(0.02)
    stage("မူရင်းအသံ နားထောင် (Whisper)")
    wf = os.path.join(work, "words.json")
    if os.path.isfile(wf):
        words = [units.Word(**w) for w in json.load(open(wf, encoding="utf-8"))]
    else:
        words = transcribe(audio, cfg["whisper"], LANGS.get(cfg["lang"], "en"), log, lambda f: prog(0.02 + 0.28 * f), cancelled)
        json.dump([w.__dict__ for w in words], open(wf, "w", encoding="utf-8"), ensure_ascii=False)
    del audio
    segs = units.drop_loops(units.atomicize(words))
    if not segs:
        raise RuntimeError("%s စကားပြောသံ မတွေ့ပါ" % cfg["lang"])
    units.assign_coverage(segs, dur)

    tr = Translator(ai, log, cancelled, cfg["lang"], cfg["glossary"])
    stage("ဇာတ်လမ်း မှတ်ဉာဏ်")
    mem = tr.story_context(segs, os.path.join(work, "story.json"), lambda f: prog(0.30 + 0.05 * f), initial=prev_memory)
    stage("မြန်မာလို ဘာသာပြန်")
    tr.translate_all(segs, mem, os.path.join(work, "translations.json"), lambda f: prog(0.35 + 0.30 * f))
    cues = make_cues(segs)
    if not cues:
        raise RuntimeError("စာတန်း တစ်ကြောင်းမှ မထွက်ပါ")

    srt = os.path.join(out_dir, base + "_mm.srt")
    with open(srt, "w", encoding="utf-8") as f:
        for n, (a, b, t) in enumerate(cues, 1):
            f.write("%d\n%s --> %s\n%s\n\n" % (n, units.fmt_ts(a), units.fmt_ts(b), t))
    if not opts.burn:
        prog(1.0)
        return srt, mem

    stage("စာတန်းပုံများ ဆွဲ")
    W, H, band, y = geometry(info, opts)
    px = H * opts.size_pct / 100.0
    pdir = os.path.join(work, "subpng")
    shutil.rmtree(pdir, ignore_errors=True)
    os.makedirs(pdir)
    blank = os.path.join(pdir, "blank.png")
    textimg.blank_png(blank, W, band)
    items = []
    for i, (a, b, t) in enumerate(cues):
        if cancelled():
            raise Stopped()
        png = os.path.join(pdir, "c_%05d.png" % i)
        textimg.sub_png(png, W, band, t, px, opts.color, opts.box)
        items.append((a, b, png))
    lst = os.path.join(pdir, "strip.txt")
    write_concat(lst, blank, items, dur + 1.0)
    graph = os.path.join(pdir, "graph.txt")
    open(graph, "w", encoding="utf-8").write(build_filter(W, band, y, cues, opts))
    prog(0.70)

    stage("စာတန်းထိုးနေတယ် (ffmpeg)")
    part = final + ".part.mp4"
    fps = int(round(info["fps"]))
    cmd = [media.FFMPEG, "-y", "-v", "error", "-i", source, "-f", "concat", "-safe", "0", "-i", lst,
           "-filter_complex_script", graph, "-map", "[v]", "-map", "0:a?", "-c:a", "copy", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "20", "-r", str(fps), "-movflags", "+faststart", "-t", "%.3f" % dur, part]
    try:
        media.run_ff_progress(cmd, dur, lambda f: prog(0.70 + 0.29 * f), cancelled, "စာတန်းထိုးခြင်း")
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    os.replace(part, final)
    shutil.rmtree(pdir, ignore_errors=True)
    prog(1.0)
    return final, mem


def run_subtitle_batch(cfg, files, opts, out_dir, log, stage, prog, cancelled, jobs_root, textimg=None):
    """Every file in order. One failing file never stops the others (except 'all Gemini keys used up', which stops everything)."""
    if not media.check_ffmpeg():
        raise RuntimeError("ffmpeg မတွေ့ပါ — ffmpeg.exe ကို app နဲ့ folder တူတူ ထားပါ")
    if not files:
        raise ValueError("ဗီဒီယို မရွေးရသေးပါ")
    keys = [k.strip() for k in cfg["keys"].split("\n") if k.strip()]
    if not keys:
        raise RuntimeError("Gemini API key မထည့်ရသေးပါ — ဆက်တင်မှာ ထည့်ပါ")
    models = [m.strip() for m in cfg["models"].split(",") if m.strip()] or DEFAULT_MODELS
    ai = Gemini(keys, models, float(cfg["min_interval"]), log, cancelled)
    dest = os.path.join(out_dir, "Subbed")
    n, ok, failed, mem = len(files), [], [], None
    t0 = time.time()
    for i, src in enumerate(files):
        if cancelled():
            raise Stopped()
        name = os.path.basename(src)
        log("━━ %d/%d · %s" % (i + 1, n, name))
        try:
            path, mem = process_one(cfg, src, opts, dest, mem, log, lambda s, tag="%d/%d" % (i + 1, n): stage("%s · %s" % (tag, s)),
                                    lambda f, i=i: prog((i + f) / n), cancelled, jobs_root, textimg, ai)
            ok.append(path)
            log("✅ %d/%d — %s" % (i + 1, n, os.path.basename(path)))
        except (Stopped, KeyPoolExhausted):
            raise
        except Exception as e:                                  # noqa: BLE001 - one bad file must not stop the rest
            failed.append(name)
            log("❌ %s — %s" % (name, str(e)[:300]))
        prog((i + 1) / n)
    log("🏁 ပြီးပါပြီ — အောင်မြင် %d · မအောင်မြင် %d (%d မိနစ်ကြာ) → %s" % (len(ok), len(failed), (time.time() - t0) / 60, dest))
    if failed:
        log("မအောင်မြင်တာတွေ: " + ", ".join(failed[:20]) + (" …" if len(failed) > 20 else "") + " — ပြန်စရင် ပြီးသားတွေကို ကျော်ပြီး ဒီအပိုင်းတွေကိုပဲ ထပ်လုပ်မယ်")
    return dest
