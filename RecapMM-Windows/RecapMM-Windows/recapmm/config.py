import json
import os

APPDIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "RecapMM")
CFG = os.path.join(APPDIR, "settings.json")
JOBS = os.path.join(os.environ.get("LOCALAPPDATA") or APPDIR, "RecapMM", "jobs")

DEFAULTS = {
    "keys": "",                 # one Gemini API key per line
    "models": "",               # empty = built-in list
    "whisper": "small",
    "lang": "English",
    "engine": "edge",           # edge | google
    "voice": "Thiha (ယောက်ျား)",
    "speed_pct": 0,
    "volume_pct": 100,
    "short_pauses": True,
    "max_height": 1080,
    "workers": 2,
    "min_interval": 4,
    "glossary": "",
    "out_dir": "",
    # Voice Clone (own GPU = your Colab)
    "own_url": "", "own_key": "", "own_gpu": "",
    "clone_steps": 16, "clone_cfg": 2.0, "clone_best": 3, "clone_soft": 1, "clone_pause": 100,
}


def load():
    c = dict(DEFAULTS)
    try:
        c.update(json.load(open(CFG, encoding="utf-8")))
    except Exception:
        pass
    return c


def save(c):
    os.makedirs(APPDIR, exist_ok=True)
    json.dump(c, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
