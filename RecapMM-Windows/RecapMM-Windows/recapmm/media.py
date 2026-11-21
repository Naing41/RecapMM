"""ffmpeg helpers: probe, audio extract/decode, Smart-Sync render (per-segment re-timing), mux."""
import json
import os
import subprocess
import sys
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .gemini import Stopped

NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _find(name):
    exe = name + (".exe" if os.name == "nt" else "")
    here = [getattr(sys, "_MEIPASS", ""), os.path.dirname(sys.executable), os.path.dirname(os.path.abspath(sys.argv[0])), os.getcwd()]
    for d in here:
        if d and os.path.isfile(os.path.join(d, exe)):
            return os.path.join(d, exe)
        if d and os.path.isfile(os.path.join(d, "ffmpeg", exe)):
            return os.path.join(d, "ffmpeg", exe)
    return name  # on PATH


FFMPEG, FFPROBE = _find("ffmpeg"), _find("ffprobe")


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, creationflags=NOWIN, **kw)


def check_ffmpeg():
    try:
        return _run([FFMPEG, "-version"]).returncode == 0
    except OSError:
        return False


def probe(path):
    r = _run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    if r.returncode != 0:
        raise IOError("ဗီဒီယို ဖတ်မရပါ: " + r.stderr.decode("utf-8", "replace")[-200:])
    j = json.loads(r.stdout)
    v = next((s for s in j["streams"] if s.get("codec_type") == "video"), None)
    if v is None:
        raise IOError("ဗီဒီယို stream မတွေ့ပါ")
    dur = float(j["format"].get("duration") or v.get("duration") or 0)
    if dur <= 0:
        raise IOError("ဗီဒီယို အရှည် ဖတ်မရပါ")
    num, _, den = (v.get("avg_frame_rate") or "30/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    if not 5 <= fps <= 120:
        fps = 30.0
    return {"duration": dur, "width": int(v["width"]), "height": int(v["height"]), "fps": fps}


def audio_16k(path, ss=None, t=None):
    """Audio of a video/audio file (optionally only [ss, ss+t]) as float32 mono 16 kHz."""
    rng = (["-ss", "%.3f" % ss] if ss else []) + (["-t", "%.3f" % t] if t else [])
    r = _run([FFMPEG, "-v", "error"] + rng + ["-i", path, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"])
    if r.returncode != 0 or not r.stdout:
        err = r.stderr.decode("utf-8", "replace")
        if "does not contain any stream" in err or "Stream map" in err:
            raise IOError("ဒီဖိုင်မှာ အသံ stream မပါပါ (စကားပြောသံ လိုတယ်)")
        raise IOError("အသံ ထုတ်မရပါ: " + err[-200:])
    return np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0


def decode_to_pcm(data, rate=24000):
    """Any audio bytes (mp3 …) -> int16 mono array at [rate]."""
    r = subprocess.run([FFMPEG, "-v", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", str(rate), "pipe:1"],
                       input=data, capture_output=True, creationflags=NOWIN)
    if r.returncode != 0 or not r.stdout:
        raise IOError("TTS အသံ decode မရပါ")
    return np.frombuffer(r.stdout, dtype="<i2").copy()


def write_wav(path, pcm, rate=24000):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.asarray(pcm, dtype="<i2").tobytes())


def trim_silence(s, rate, min_duration):
    """Drops the silence TTS adds around each clip (port of trimSilence)."""
    win = rate // 100
    count = len(s) // win
    if count == 0:
        return s
    blocks = s[:count * win].astype(np.float64).reshape(count, win)
    rms = np.sqrt((blocks ** 2).mean(axis=1)) / 32768.0
    idx = np.nonzero(rms > 10 ** (-50 / 20))[0]
    if len(idx) == 0:
        return s
    dur = len(s) / rate
    start = max(0.0, idx[0] * win / rate - 0.05)
    end = min(dur, (idx[-1] + 1) * win / rate + 0.20)
    if end - start < min_duration:
        end = min(dur, start + min_duration)
        start = max(0.0, end - min_duration)
    a = min(max(int(round(start * rate)), 0), len(s))
    b = min(max(int(round(end * rate)), a), len(s))
    return s[a:b]


def render_segment(source, out, item, info, fps, h, log=None, preset="veryfast", crf=21):
    """One piece of the picture -> an .mp4 of exactly (o1 - o0) seconds.
    item = (o0, o1, video_start, speed[, zoom0, zoom1, fade_in, fade_out]); speed 0 = hold the first frame (freeze)."""
    o0, o1, vs, speed = item[:4]
    z0, z1, fdin, fdout = (list(item[4:8]) + [1.0, 1.0, 0.0, 0.0][len(item) - 4:])[:4]
    t = max(0.04, o1 - o0)
    freeze = speed < 0.001
    if freeze:
        chain = ["trim=end_frame=1", "setpts=PTS-STARTPTS", "tpad=stop_mode=clone:stop_duration=%.2f" % (t + 1)]
        span = 1.0
    else:
        chain = ["setpts=PTS/%.6f" % speed, "tpad=stop_mode=clone:stop_duration=4"]
        span = t * speed + 0.1
    zoom = abs(z1 - z0) > 1e-6 or abs(z0 - 1.0) > 1e-6
    w = max(2, int(round(info["width"] * h / float(info["height"]) / 2.0)) * 2)
    chain += ["fps=%d" % fps, ("scale=%d:%d:flags=bicubic" % (w, h)) if zoom else ("scale=-2:%d:flags=bicubic" % h)]
    # fades first, zoom last: fade after a frame-by-frame scale crashes some ffmpeg builds (heap corruption)
    if fdin > 0:
        chain.append("fade=t=in:st=0:d=%.3f" % fdin)
    if fdout > 0:
        chain.append("fade=t=out:st=%.3f:d=%.3f" % (max(0.0, t - fdout), fdout))
    if zoom:
        zz = "(%.5f+(%.5f)*t/%.4f)" % (z0, z1 - z0, t)
        chain += ["scale=w='trunc(iw*%s/2)*2':h='trunc(ih*%s/2)*2':eval=frame" % (zz, zz), "crop=%d:%d" % (w, h)]
    chain.append("format=yuv420p")
    part = out + ".part.mp4"
    cmd = [FFMPEG, "-y", "-v", "error", "-ss", "%.3f" % vs, "-t", "%.3f" % span, "-i", source, "-an",
           "-vf", ",".join(chain), "-t", "%.3f" % t, "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-r", str(fps),
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", part]
    r = _run(cmd)
    if r.returncode != 0:
        raise IOError("ffmpeg render မအောင်ပါ: %s" % r.stderr.decode("utf-8", "replace")[-300:])
    os.replace(part, out)
    return out


def concat_copy(files, out_path, workdir):
    lst = os.path.join(workdir, "list_%d.txt" % abs(hash(out_path)))
    with open(lst, "w", encoding="utf-8") as f:
        for p in files:
            f.write("file '%s'\n" % p.replace("\\", "/").replace("'", "'\\''"))
    r = _run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out_path])
    if r.returncode != 0:
        raise IOError("ရုပ် ပေါင်းမရပါ: " + r.stderr.decode("utf-8", "replace")[-300:])


def render_video(source, plan, out_path, workdir, info, log, progress, cancelled, max_height=1080, workers=2, crf=21, preset="veryfast"):
    """plan: list of (out_start, out_end, video_start, speed[, zoom0, zoom1, fade_in, fade_out]). Each piece of the picture is cut,
    re-timed to its narration clip and encoded; the pieces are then joined without re-encoding."""
    fps = int(round(info["fps"]))
    h = min(max_height, info["height"])
    h -= h % 2
    os.makedirs(workdir, exist_ok=True)
    total = len(plan)
    done = [0]

    def one(i):
        out = os.path.join(workdir, "seg_%05d.mp4" % i)
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            return out
        if cancelled():
            raise Stopped()
        render_segment(source, out, plan[i], info, fps, h, log, preset, crf)
        done[0] += 1
        if done[0] % 10 == 0 or done[0] == total:
            log("🎞 render %d/%d" % (done[0], total))
        progress(done[0] / total)
        return out

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        files = list(ex.map(one, range(total)))
    concat_copy(files, out_path, workdir)


def mux(video, wav, out_path, bitrate="192k"):
    r = _run([FFMPEG, "-y", "-v", "error", "-i", video, "-i", wav, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
              "-c:a", "aac", "-b:a", bitrate, "-shortest", "-movflags", "+faststart", out_path])
    if r.returncode != 0:
        raise IOError("အသံ+ရုပ် ပေါင်းမရပါ: " + r.stderr.decode("utf-8", "replace")[-300:])


# ---------------------------------------------------------------- extra helpers for the other tools
def keyframes(path):
    """Key-frame times (seconds) of the first video stream, from packet flags (no decoding, fast)."""
    r = _run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,flags",
              "-of", "csv=p=0", path])
    if r.returncode != 0:
        raise IOError("Key frame ရှာမရပါ: " + r.stderr.decode("utf-8", "replace")[-200:])
    out = []
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        t, _, fl = line.partition(",")
        if "K" in fl:
            try:
                out.append(float(t))
            except ValueError:
                pass
    return sorted(set(out))


def encode_audio(wav, out_path, bitrate="192k"):
    """wav -> .mp3 / .m4a (chosen by the extension of out_path)."""
    codec = ["-c:a", "libmp3lame", "-b:a", bitrate] if out_path.lower().endswith(".mp3") else ["-c:a", "aac", "-b:a", bitrate]
    r = _run([FFMPEG, "-y", "-v", "error", "-i", wav] + codec + [out_path])
    if r.returncode != 0:
        raise IOError("အသံ ဖိုင် ပြုလုပ်မရပါ: " + r.stderr.decode("utf-8", "replace")[-300:])


def audio_pcm(path, rate):
    """Any audio/video file -> int16 mono array at [rate]."""
    r = _run([FFMPEG, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "pipe:1"])
    if r.returncode != 0 or not r.stdout:
        raise IOError("Audio ဖတ်မရပါ: " + r.stderr.decode("utf-8", "replace")[-200:])
    return np.frombuffer(r.stdout, dtype="<i2").copy()


def run_ff(cmd, what="ffmpeg"):
    r = _run(cmd)
    if r.returncode != 0:
        raise IOError("%s မအောင်ပါ: %s" % (what, r.stderr.decode("utf-8", "replace")[-400:]))


def run_ff_progress(cmd, total_sec, on_progress, cancelled, what="ffmpeg"):
    """Runs ffmpeg and reports out_time / total_sec (0..1). Kills it when [cancelled]() becomes true."""
    full = [cmd[0], "-progress", "pipe:1", "-nostats"] + cmd[1:]
    p = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NOWIN)
    err = []
    import threading

    def drain():
        err.append(p.stderr.read())
    th = threading.Thread(target=drain, daemon=True)
    th.start()
    try:
        for line in p.stdout:
            if cancelled():
                p.kill()
                raise Stopped()
            s = line.decode("utf-8", "replace").strip()
            if s.startswith("out_time_us=") or s.startswith("out_time_ms="):
                try:
                    on_progress(min(1.0, max(0.0, float(s.split("=")[1]) / 1e6 / max(total_sec, 0.1))))
                except ValueError:
                    pass
        p.wait()
    finally:
        if p.poll() is None:
            p.kill()
    th.join(timeout=2)
    if p.returncode != 0:
        raise IOError("%s မအောင်ပါ: %s" % (what, (err[0] if err else b"").decode("utf-8", "replace")[-400:]))
