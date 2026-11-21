"""One job = one folder. Every stage writes its result there, so a stopped job resumes where it was."""
import hashlib
import json
import os
import re
import shutil

import numpy as np

from . import media, units
from .gemini import Gemini, DEFAULT_MODELS, Stopped
from .translator import Translator
from .tts import EdgeTts, GoogleTts, VOICES, RATE

LANGS = {"English": "en", "Chinese": "zh", "Auto-detect": None, "Hindi": "hi", "Korean": "ko", "Japanese": "ja", "Thai": "th",
         "Vietnamese": "vi", "Indonesian": "id", "Malay": "ms", "Spanish": "es", "French": "fr", "German": "de", "Russian": "ru",
         "Portuguese": "pt", "Arabic": "ar", "Turkish": "tr", "Burmese": "my"}
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
STAGES = ["အသံထုတ်ယူ", "မူရင်းအသံ နားထောင် (Whisper)", "ဇာတ်လမ်း မှတ်ဉာဏ်", "မြန်မာလို ဘာသာပြန်",
          "မြန်မာအသံ ထုတ်", "ရုပ် render (Smart Sync)", "ပေါင်းစပ်"]
WEIGHTS = [0.03, 0.27, 0.05, 0.15, 0.20, 0.27, 0.03]


def safe_name(s, default="video", n=60):
    """File-name safe, but keeps Myanmar combining marks (\\w would turn them into '_')."""
    return re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", s).strip(" ._")[:n] or default


def job_dir(jobs_root, source):
    st = os.stat(source)
    h = hashlib.sha1(("%s|%d|%d" % (os.path.abspath(source), st.st_size, int(st.st_mtime))).encode()).hexdigest()[:8]
    base = safe_name(os.path.splitext(os.path.basename(source))[0], "video", 40)
    d = os.path.join(jobs_root, "%s_%s" % (base, h))
    os.makedirs(d, exist_ok=True)
    return d


def transcribe(audio, model_name, lang, log, progress, cancelled):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError("faster-whisper မတပ်ထားပါ")
    device, ctype = "cpu", "int8"
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            device, ctype = "cuda", "float16"
    except Exception:
        pass
    log("🧠 Whisper '%s' (%s) ဖွင့်နေတယ် — ပထမဆုံးအကြိမ်ဆိုရင် model ဒေါင်းမယ် (အင်တာနက်လို)" % (model_name, device))
    try:
        model = WhisperModel(model_name, device=device, compute_type=ctype)
    except Exception:
        if device == "cuda":            # CUDA libs missing -> CPU
            device, ctype = "cpu", "int8"
            model = WhisperModel(model_name, device=device, compute_type=ctype)
        else:
            raise
    total = len(audio) / 16000.0
    segs, _ = model.transcribe(audio, language=lang, word_timestamps=True, vad_filter=True,
                               beam_size=5, condition_on_previous_text=False)
    words = []
    for s in segs:
        if cancelled():
            raise Stopped()
        for w in (s.words or []):
            t = (w.word or "").strip()
            if t:
                words.append(units.Word(t, float(w.start), float(w.end)))
        progress(min(1.0, s.end / total))
    return words


class Pipeline:
    def __init__(self, cfg, source, out_dir, log, on_stage, on_progress, cancelled, jobs_root):
        self.cfg, self.source, self.out_dir = cfg, source, out_dir
        self.log, self.on_stage, self.on_progress, self.cancelled = log, on_stage, on_progress, cancelled
        self.dir = job_dir(jobs_root, source)
        self.stage = 0

    def _begin(self, i):
        self.stage = i
        self.log("▶ " + STAGES[i])
        self.on_stage(STAGES[i], sum(WEIGHTS[:i]))

    def _rep(self, f):
        f = min(max(f, 0.0), 1.0)
        self.on_progress(sum(WEIGHTS[:self.stage]) + WEIGHTS[self.stage] * f)

    def _p(self, *a):
        return os.path.join(self.dir, *a)

    def run(self):
        cfg = self.cfg
        keys = [k.strip() for k in cfg["keys"].split("\n") if k.strip()]
        if not keys:
            raise RuntimeError("Gemini API key မထည့်ရသေးပါ — Settings မှာ ထည့်ပါ")
        if not media.check_ffmpeg():
            raise RuntimeError("ffmpeg မတွေ့ပါ — ffmpeg.exe ကို app နဲ့ folder တူတူ ထားပါ")
        info = media.probe(self.source)
        dur = info["duration"]
        self.log("🎬 %.1f မိနစ် · %dx%d" % (dur / 60, info["width"], info["height"]))

        # 1. audio
        self._begin(0)
        audio = media.audio_16k(self.source)
        self._rep(1)

        # 2. Whisper
        self._begin(1)
        wf = self._p("words.json")
        if os.path.isfile(wf):
            self.log("♻️ Whisper ရလဒ် ရှိပြီးသား")
            words = [units.Word(**w) for w in json.load(open(wf, encoding="utf-8"))]
        else:
            words = transcribe(audio, cfg["whisper"], LANGS.get(cfg["lang"], "en"), self.log, self._rep, self.cancelled)
            json.dump([w.__dict__ for w in words], open(wf, "w", encoding="utf-8"), ensure_ascii=False)
        del audio
        segs = units.drop_loops(units.atomicize(words))
        if not segs:
            raise RuntimeError("%s စကားပြောသံ မတွေ့ပါ" % cfg["lang"])
        units.assign_coverage(segs, dur)
        self.log("🧩 sync unit %d ခု" % len(segs))
        open(self._p("whisper_transcript.txt"), "w", encoding="utf-8").write(
            "\n".join("[%s] %s" % (units.timestamp(s), s.text) for s in segs))

        # 3 + 4. Gemini
        models = [m.strip() for m in cfg["models"].split(",") if m.strip()] or DEFAULT_MODELS
        ai = Gemini(keys, models, float(cfg["min_interval"]), self.log, self.cancelled)
        tr = Translator(ai, self.log, self.cancelled, cfg["lang"], cfg["glossary"])
        self._begin(2)
        memory = tr.story_context(segs, self._p("story.json"), self._rep)
        self._begin(3)
        tr.translate_all(segs, memory, self._p("translations.json"), self._rep)
        json.dump([{"id": s.id, "timestamp": units.timestamp(s), "source": s.text, "translation": s.translation} for s in segs],
                  open(self._p("timestamp_translations.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)

        # 5. TTS
        self._begin(4)
        tts_dir = self._p("tts")
        os.makedirs(tts_dir, exist_ok=True)
        engine = EdgeTts(self.log, self.cancelled) if cfg["engine"] == "edge" else GoogleTts(self.log, self.cancelled)
        voice = VOICES.get(cfg["voice"], "my-MM-ThihaNeural")
        speed = int(cfg["speed_pct"]) if cfg["engine"] == "edge" else 0

        def tts_file(s):
            tag = hashlib.sha1(("%s|%s|%d|%s" % (cfg["engine"], voice, speed, s.translation)).encode()).hexdigest()[:10]
            return os.path.join(tts_dir, "%05d_%s.npy" % (s.id, tag))

        for i, s in enumerate(segs):
            if self.cancelled():
                raise Stopped()
            f = tts_file(s)
            if os.path.isfile(f):
                continue
            if not any(ch.isalnum() for ch in s.translation):          # e.g. "♪" - nothing to say: keep the timing with silence
                pcm = np.zeros(int(RATE * max(0.4, s.end - s.start)), np.int16)
            else:
                pcm = engine.synth(s.translation, voice, speed)
            if len(pcm) < RATE // 20:
                raise IOError("TTS clip တိုလွန်းနေတယ် (ID %d)" % s.id)
            np.save(f + ".tmp.npy", pcm)
            os.replace(f + ".tmp.npy", f)
            if (i + 1) % 20 == 0 or i + 1 == len(segs):
                self.log("🔊 အသံ %d/%d" % (i + 1, len(segs)))
            self._rep((i + 1) / len(segs))

        # 6. narration + Smart Sync plan
        self._begin(5)
        gain = int(cfg["volume_pct"]) / 100.0
        out_t, plan, subs, chunks = 0.0, [], [], []
        for s in segs:
            clip = np.load(tts_file(s))
            window = max(0.05, s.vend - s.vstart)
            if cfg["short_pauses"]:
                clip = media.trim_silence(clip, RATE, window / 1.25)
            clip = np.clip(clip.astype(np.float32) * gain, -32768, 32767).astype(np.int16)
            chunks.append(clip)
            t = len(clip) / RATE
            applied = max(t / window, 1 / 1.25)          # never faster than 1.25x
            plan.append((out_t, out_t + t, s.vstart, 1.0 / applied))
            subs.append((out_t, out_t + t, s.translation))
            out_t += t
        self.log("⏱ output %.1f မိနစ် (မူရင်း %.1f)" % (out_t / 60, dur / 60))
        wav = self._p("narration.wav")
        media.write_wav(wav, np.concatenate(chunks), RATE)
        del chunks
        seg_dir = self._p("segments")
        video_only = self._p("video_only.mp4")
        for p in (video_only,):
            if os.path.isfile(p):
                os.remove(p)
        media.render_video(self.source, plan, video_only, seg_dir, info, self.log, self._rep, self.cancelled,
                           max_height=int(cfg["max_height"]), workers=int(cfg["workers"]))

        # 7. mux
        self._begin(6)
        name = safe_name(os.path.splitext(os.path.basename(self.source))[0])
        os.makedirs(self.out_dir, exist_ok=True)
        final = os.path.join(self.out_dir, "%s_Myanmar.mp4" % name)
        media.mux(video_only, wav, final)
        with open(os.path.join(self.out_dir, "%s_Myanmar.srt" % name), "w", encoding="utf-8") as f:
            for i, (a, b, t) in enumerate(subs, 1):
                f.write("%d\n%s --> %s\n%s\n\n" % (i, units.fmt_ts(a), units.fmt_ts(b), t))
        # free the big temporary files; words / translations / tts clips stay so a re-run is cheap
        shutil.rmtree(seg_dir, ignore_errors=True)
        shutil.rmtree(tts_dir, ignore_errors=True)
        for p in (video_only, wav):
            try:
                os.remove(p)
            except OSError:
                pass
        self._rep(1)
        self.log("✅ ပြီးပါပြီ — " + final)
        return final
