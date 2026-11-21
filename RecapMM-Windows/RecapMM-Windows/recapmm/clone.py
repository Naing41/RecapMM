"""Voice Clone on YOUR OWN GPU (a free Colab running the notebook). The PC talks to Colab directly through the Cloudflare tunnel the
notebook opens — no server of ours is involved. Reference voices stay on this PC; the worker gets them once per session."""
import base64
import hashlib
import io
import json
import os
import re
import time
import wave

import numpy as np
import requests

from . import config, media
from .gemini import Gemini, DEFAULT_MODELS, Stopped
from .tts import RATE

VOICE_DIR = os.path.join(config.APPDIR, "clone_own")
MAX_REF_SEC = 20
NOTEBOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "RecapMM_Voice_Clone.ipynb")
_sent = set()
last_sim = None


class CloneFatal(IOError):
    """Retrying cannot help."""


# ---------------------------------------------------------------- connection
def parse_code(raw):
    """'RCP1:<base64url json {u,k,g}>' anywhere inside [raw] -> (url, key, gpu) or None."""
    m = re.search(r"RCP1:([A-Za-z0-9_\-]+)", raw or "")
    if not m:
        return None
    s = m.group(1)
    try:
        j = json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8"))
        url, key = str(j["u"]).strip().rstrip("/"), str(j["k"]).strip()
    except Exception:
        return None
    if not url.startswith("https://") or not key:
        return None
    return url, key, str(j.get("g", ""))


def check(url, key):
    """(ok, message). ok = reachable, key accepted and the model is loaded."""
    try:
        r = requests.get(url + "/check", headers={"X-Key": key}, timeout=(15, 25))
        if r.status_code == 401:
            return False, "Key မကိုက်ပါ — ကုဒ်အသစ် ထပ်ယူပါ"
        if r.status_code != 200:
            return False, "GPU မတွေ့ပါ (Colab ရပ်သွားတာ ဖြစ်နိုင်တယ်) — ကုဒ်အသစ် ထပ်ယူပါ"
        j = r.json()
        if j.get("loaded"):
            return True, str(j.get("gpu", ""))
        return False, "Model load လုပ်နေဆဲ — ခဏနေ ပြန်စစ်ပါ"
    except Exception:
        return False, "ချိတ်မရပါ — Colab မှာ cell ပြေးနေလား စစ်ပါ"


def forget_sent():
    _sent.clear()


# ---------------------------------------------------------------- reference voices (kept on this PC)
def _p(id_, ext):
    return os.path.join(VOICE_DIR, id_ + "." + ext)


def _safe_id(id_):
    return bool(id_) and id_.isalnum()


def prepare_ref(pcm, max_sec=15):
    """Best speech part of a recording, silence cut, loudness evened. pcm: int16 mono 16 kHz. -> (int16 array, note or None)."""
    s = np.asarray(pcm, dtype=np.int16)
    n = len(s)
    if n < 16000:
        return s, "အသံ တိုလွန်းတယ်"
    fl = 320
    frames = n // fl
    x = s[:frames * fl].astype(np.float64).reshape(frames, fl)
    rms = np.sqrt((x ** 2).mean(axis=1))
    srt = np.sort(rms)
    floor = srt[min(max(int(frames * 0.10), 0), frames - 1)]
    loud = srt[min(max(int(frames * 0.90), 0), frames - 1)]
    thr = max(floor * 3.0, loud * 0.12)
    speech = rms > thr
    win = min(frames, max_sec * 50)
    pre = np.concatenate([[0], np.cumsum(speech.astype(np.int64))])
    best, best_cnt, st = 0, -1, 0
    while st + win <= frames:
        c = pre[st + win] - pre[st]
        if c > best_cnt:
            best_cnt, best = c, st
        st += 25
    a, b = best, best + win
    while a < b - 1 and not speech[a]:
        a += 1
    while b > a + 1 and not speech[b - 1]:
        b -= 1
    a, b = max(best, a - 8), min(best + win, b + 8)
    frm, to = a * fl, min(n, b * fl)
    seg = s[frm:max(frm + 1, to)]
    peak = max(1, int(np.abs(seg.astype(np.int32)).max()))
    clipped = int((np.abs(seg.astype(np.int32)) >= 32000).sum())
    gain = min(8.0, max(0.5, 27800.0 / peak))
    out = np.clip(seg.astype(np.float64) * gain, -32768, 32767).astype(np.int16)
    speech_sec = float(speech[a:b].sum()) / 50.0
    snr = loud / floor if floor > 0 else 99.0
    notes = []
    if speech_sec < 4:
        notes.append("စကားပြောတဲ့ အပိုင်း %.0f စက္ကန့်ပဲ ရှိတယ် — ၁၀–၁၅ စက္ကန့် ရှိရင် ပိုတူ" % speech_sec)
    if snr < 5:
        notes.append("ဆူညံသံ / ဂီတ ပါနေနိုင်တယ် — ရှင်းတဲ့ အသံသုံးရင် ပိုတူ")
    if clipped > len(seg) / 200:
        notes.append("အသံ ကျယ်လွန်းလို့ ကွဲနေတယ်")
    return out, (" · ".join(notes) or None)


def list_voices():
    """Newest first: [{'id','name','sec','text'}]."""
    if not os.path.isdir(VOICE_DIR):
        return []
    files = [f for f in os.listdir(VOICE_DIR) if f.endswith(".pcm")]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(VOICE_DIR, f)), reverse=True)
    out = []
    for f in files:
        i = f[:-4]
        name = open(_p(i, "name"), encoding="utf-8").read().strip() if os.path.isfile(_p(i, "name")) else "voice"
        txt = open(_p(i, "txt"), encoding="utf-8").read() if os.path.isfile(_p(i, "txt")) else ""
        out.append({"id": i, "name": name or "voice", "sec": os.path.getsize(os.path.join(VOICE_DIR, f)) / 32000.0, "text": txt})
    return out


def add_voice_from_file(path, name):
    """Any audio/video file -> a saved reference voice. Returns (id, note)."""
    pcm = media.audio_pcm(path, 16000)[:16000 * 600]            # the first 10 minutes are enough to find the best speech in
    prep, note = prepare_ref(pcm, 15)
    prep = prep[:16000 * MAX_REF_SEC]
    if len(prep) < 16000 * 3:
        raise IOError("စကားပြောတဲ့ အပိုင်း ၃ စက္ကန့်ထက် ပိုရှည်ရမယ် (၁၀ စက္ကန့်လောက် အကောင်းဆုံး)")
    raw = prep.astype("<i2").tobytes()
    id_ = hashlib.sha1(raw[:200000] + str(len(raw)).encode()).hexdigest()[:16]
    os.makedirs(VOICE_DIR, exist_ok=True)
    open(_p(id_, "pcm"), "wb").write(raw)
    open(_p(id_, "name"), "w", encoding="utf-8").write(name[:60])
    for v in list_voices()[20:]:                                 # keep the newest 20
        delete_voice(v["id"])
    return id_, note


def set_text(id_, text):
    if not _safe_id(id_) or not os.path.isfile(_p(id_, "pcm")):
        return False
    t = text.strip()[:600]
    if t:
        open(_p(id_, "txt"), "w", encoding="utf-8").write(t)
    elif os.path.isfile(_p(id_, "txt")):
        os.remove(_p(id_, "txt"))
    return True


def ref_pcm(id_):
    return open(_p(id_, "pcm"), "rb").read() if _safe_id(id_) and os.path.isfile(_p(id_, "pcm")) else None


def delete_voice(id_):
    if not _safe_id(id_):
        return False
    ok = os.path.isfile(_p(id_, "pcm"))
    for e in ("pcm", "name", "txt"):
        if os.path.isfile(_p(id_, e)):
            os.remove(_p(id_, e))
    return ok


def wav16k(pcm_bytes):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm_bytes)
    return buf.getvalue()


def transcribe(cfg, pcm_bytes, log=lambda s: None):
    """What the reference clip says — Gemini listens (Burmese included). The person can correct it."""
    keys = [k.strip() for k in cfg["keys"].split("\n") if k.strip()]
    if not keys:
        raise IOError("Gemini API key မထည့်ရသေးပါ — ဆက်တင်မှာ ထည့်ပါ")
    models = [m.strip() for m in cfg["models"].split(",") if m.strip()] or DEFAULT_MODELS
    ai = Gemini(keys, models, float(cfg["min_interval"]) * 1.0, log, lambda: False)
    o = ai.json("Transcribe exactly what the speaker says in this audio, word for word, in the language and script that is spoken "
                "(Burmese in Myanmar Unicode, never Zawgyi). Add normal punctuation. No translation, no notes, nothing that is not said. "
                'Return JSON only: {"text": "..."}', audio=wav16k(pcm_bytes))
    return str(o.get("text", "")).strip() if isinstance(o, dict) else ""


# ---------------------------------------------------------------- speaking
def _post(url, key, rid, text, steps, cfg_scale, ref, prompt, best):
    j = {"ref_id": rid, "text": text, "steps": int(steps), "cfg": float(cfg_scale), "prompt_text": prompt, "best_of": int(best)}
    if ref is not None:
        j["ref"] = base64.b64encode(wav16k(ref)).decode()
    return requests.post(url + "/synth", headers={"X-Key": key, "Content-Type": "application/json"}, data=json.dumps(j),
                         timeout=(20, 320))


def synth_once(cfg, text, voice_id, steps, cfg_scale, best):
    """One line -> int16 mono at 24 kHz. CloneFatal when retrying cannot help, IOError when it might."""
    global last_sim
    url, key = cfg.get("own_url", ""), cfg.get("own_key", "")
    if not url or not key:
        raise CloneFatal("ကိုယ်ပိုင် GPU မချိတ်ရသေးပါ")
    ref = ref_pcm(voice_id)
    if ref is None:
        raise CloneFatal("အသံနမူနာ ဒီ PC ထဲမှာ မရှိတော့ပါ — ပြန်ရွေးပါ")
    rid = "own_" + voice_id
    prompt = open(_p(voice_id, "txt"), encoding="utf-8").read() if os.path.isfile(_p(voice_id, "txt")) else ""   # transcript => "ultimate cloning"
    try:
        r = _post(url, key, rid, text, steps, cfg_scale, None if rid in _sent else ref, prompt, best)
        if r.status_code == 409:                       # the worker restarted and forgot the reference
            r = _post(url, key, rid, text, steps, cfg_scale, ref, prompt, best)
    except requests.RequestException as e:
        raise IOError("ချိတ်မရ: %s" % str(e)[:120])
    if r.status_code == 200:
        _sent.add(rid)
        try:
            last_sim = float(r.headers.get("X-Sim"))
        except (TypeError, ValueError):
            last_sim = None
        return np.frombuffer(r.content[:len(r.content) // 2 * 2], dtype="<i2").copy()
    if r.status_code == 401:
        raise CloneFatal("Key မကိုက်ပါ — Colab က ကုဒ်အသစ် ထပ်ယူပါ")
    if r.status_code in (530, 404):
        raise CloneFatal("GPU မတွေ့တော့ပါ (Colab ပြတ်သွားပြီ) — cell ပြန်ပြေးပြီး ကုဒ်အသစ် ထပ်ထည့်ပါ")
    if r.status_code == 503:
        raise IOError("GPU model load လုပ်နေဆဲ")
    raise IOError("GPU HTTP %d" % r.status_code)


def soften(pcm, level, rate=RATE):
    """Takes the edge off a harsh / shrill clone: one-pole low-pass blended with the original."""
    if level <= 0 or len(pcm) == 0:
        return pcm
    k = {1: 0.25, 2: 0.45}.get(level, 0.65)
    a = float(np.exp(-2.0 * np.pi * 4000.0 / rate))
    x = pcm.astype(np.float64).tolist()
    lp, out = 0.0, [0.0] * len(x)
    for i, v in enumerate(x):
        lp = (1 - a) * v + a * lp
        out[i] = v - k * (v - lp)
    return np.clip(np.array(out), -32768, 32767).astype(np.int16)


def _sleep(sec, cancelled):
    end = time.time() + sec
    while time.time() < end:
        if cancelled():
            raise Stopped()
        time.sleep(min(0.25, end - time.time()))


def speak(cfg, text, voice_id, steps, cfg_scale, best, soft, cancelled=lambda: False, log=lambda s: None):
    for attempt in range(3):
        if cancelled():
            raise Stopped()
        try:
            return soften(synth_once(cfg, text, voice_id, steps, cfg_scale, best), soft)
        except CloneFatal:
            raise
        except IOError as e:
            err = str(e)
        log("↻ Voice Clone ပြန်ကြိုးစားနေတယ် (%s)" % err)
        _sleep(2.0 * (attempt + 1), cancelled)
    raise IOError("Voice Clone မရပါ (%s)" % err)


def run_clone(cfg, text, voice_id, steps, cfg_scale, best, soft, pause_ms, out_dir, log, stage, prog, cancelled, jobs_root):
    """Text in, a file in the person's cloned voice out (.mp3). Finished lines stay on disk, so a stopped run resumes."""
    from .tools import split_text            # same text splitting as the TTS tool
    if not media.check_ffmpeg():
        raise RuntimeError("ffmpeg မတွေ့ပါ — ffmpeg.exe ကို app နဲ့ folder တူတူ ထားပါ")
    pieces = split_text(text, 220)
    if not pieces:
        raise ValueError("စာ ရိုက်ထည့်ပါ")
    prompt = open(_p(voice_id, "txt"), encoding="utf-8").read() if os.path.isfile(_p(voice_id, "txt")) else ""
    base = hashlib.sha1(("%s|%s|%d|%.2f|%d|%s" % (text, voice_id, steps, cfg_scale, best, prompt)).encode()).hexdigest()[:16]
    cache = os.path.join(jobs_root, "clone_" + base)
    os.makedirs(cache, exist_ok=True)
    stage("အသံ ထုတ်နေတယ် (Colab GPU)", 0.0)
    chunks, t0 = [], time.time()
    for i, (t, gap) in enumerate(pieces):
        if cancelled():
            raise Stopped()
        f = os.path.join(cache, "%05d.npy" % i)
        if os.path.isfile(f):
            raw = np.load(f)
        else:
            raw = synth_once_retry(cfg, t, voice_id, steps, cfg_scale, best, cancelled, log)
            np.save(f + ".tmp.npy", raw)
            os.replace(f + ".tmp.npy", f)
        chunks.append(soften(raw, soft))
        chunks.append(np.zeros(RATE * (pause_ms * (3 if gap >= 650 else 1)) // 1000, dtype=np.int16))
        done = i + 1
        eta = (time.time() - t0) / done * (len(pieces) - done)
        log("🧬 %d/%d%s" % (done, len(pieces), (" · ကျန် ~%d စက္ကန့်" % eta) if done < len(pieces) else ""))
        prog(done / len(pieces) * 0.95)
    wav = os.path.join(cache, "all.wav")
    media.write_wav(wav, np.concatenate(chunks), RATE)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "clone_%d.mp3" % int(time.time()))
    stage("ဖိုင် ပြုလုပ်နေတယ်", 0.95)
    media.encode_audio(wav, out)
    import shutil
    shutil.rmtree(cache, ignore_errors=True)
    prog(1.0)
    log("✅ " + out)
    return out


def synth_once_retry(cfg, text, voice_id, steps, cfg_scale, best, cancelled, log):
    """Raw (not softened) clip with retries — what gets cached."""
    err = ""
    for attempt in range(3):
        if cancelled():
            raise Stopped()
        try:
            return synth_once(cfg, text, voice_id, steps, cfg_scale, best)
        except CloneFatal:
            raise
        except IOError as e:
            err = str(e)
        log("↻ Voice Clone ပြန်ကြိုးစားနေတယ် (%s)" % err)
        _sleep(2.0 * (attempt + 1), cancelled)
    raise IOError("Voice Clone မရပါ (%s)" % err)
