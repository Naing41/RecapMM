"""Burmese TTS engines that need no server: Microsoft Edge neural voices (edge-tts) and Google Translate read-aloud."""
import asyncio
import re
import time
import unicodedata

import numpy as np
import requests

from .gemini import Stopped
from .media import decode_to_pcm

VOICES = {"Thiha (ယောက်ျား)": "my-MM-ThihaNeural", "Nilar (မိန်းမ)": "my-MM-NilarNeural"}
RATE = 24000


def split_for_tts(text, mx=170):
    t = re.sub(r"\s+", " ", text).strip()
    out, cur = [], ""

    def flush():
        nonlocal cur
        c = cur.strip()
        if any(ch.isalnum() for ch in c):
            out.append(c)
        cur = ""

    for m in re.finditer(r"[^။.!?၊,;]+[။.!?၊,;]*", t):
        sentence = m.group(0)
        if len(cur) + len(sentence) > mx:
            flush()
        if len(sentence) <= mx:
            cur += sentence
            continue
        for w in sentence.split(" "):
            if len(cur) + len(w) + 1 > mx:
                flush()
            cur += w + " "
    flush()
    return out


def _clean(text):
    t = unicodedata.normalize("NFC", text)
    return "".join(c if (c in "\n\t" or ord(c) >= 32) else " " for c in t)


def _sleep(sec, cancelled):
    end = time.time() + sec
    while time.time() < end:
        if cancelled():
            raise Stopped()
        time.sleep(min(0.25, end - time.time()))


def _join(parts, gap_s):
    gap = np.zeros(int(RATE * gap_s), dtype=np.int16)
    out = []
    for i, p in enumerate(parts):
        out.append(p)
        if i < len(parts) - 1:
            out.append(gap)
    return np.concatenate(out) if out else np.zeros(0, dtype=np.int16)


class EdgeTts:
    """Microsoft Edge read-aloud. The edge-tts library makes its own access token on this PC — no server needed."""
    name = "Microsoft (Edge)"

    def __init__(self, log, cancelled):
        self.log, self.cancelled = log, cancelled
        self.last = 0.0

    async def _one(self, piece, voice, rate_pct):
        import edge_tts
        rate = ("+" if rate_pct >= 0 else "") + "%d%%" % rate_pct
        buf = b""
        async for ch in edge_tts.Communicate(piece, voice, rate=rate).stream():
            if ch["type"] == "audio":
                buf += ch["data"]
        return buf

    def _fetch(self, piece, voice, rate_pct):
        err = ""
        for attempt in range(5):
            if self.cancelled():
                raise Stopped()
            wait = self.last + 0.3 - time.time()
            if wait > 0:
                _sleep(wait, self.cancelled)
            self.last = time.time()
            try:
                b = asyncio.run(self._one(piece, voice, rate_pct))
                if len(b) > 400:
                    return b
                err = "အသံ ဗလာ"
            except Exception as e:
                err = str(e)[:150]
            self.log("⚠️ Edge TTS ပြန်စမ်းမယ် (%s)" % err)
            _sleep(1.5 * (attempt + 1), self.cancelled)
        raise IOError("Edge TTS မရပါ (%s) — အင်တာနက်စစ်ပါ၊ မရသေးရင် Google (အခမဲ့) engine ကို ပြောင်းပါ" % err)

    def synth(self, text, voice, rate_pct=0):
        parts = split_for_tts(_clean(text), 400)
        if not parts:
            raise ValueError("TTS စာသား ပြောလို့မရပါ: '%s'" % text)
        return _join([decode_to_pcm(self._fetch(p, voice, rate_pct), RATE) for p in parts], 0.10)


class GoogleTts:
    """Google Translate read-aloud (language 'my'). Free, robotic voice, unofficial."""
    name = "Google (အခမဲ့)"

    def __init__(self, log, cancelled):
        self.log, self.cancelled = log, cancelled
        self.last = 0.0
        self.s = requests.Session()

    def _fetch(self, piece):
        err = ""
        for attempt in range(5):
            if self.cancelled():
                raise Stopped()
            wait = self.last + 0.7 - time.time()
            if wait > 0:
                _sleep(wait, self.cancelled)
            self.last = time.time()
            try:
                r = self.s.get("https://translate.google.com/translate_tts",
                               params={"ie": "UTF-8", "client": "tw-ob", "tl": "my", "q": piece},
                               headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0",
                                        "Referer": "https://translate.google.com/"}, timeout=40)
                if r.status_code == 200 and len(r.content) > 400:
                    return r.content
                err = "HTTP %d" % r.status_code
                if r.status_code == 429:
                    self.log("⏳ Google TTS 429 — ခဏစောင့်မယ်")
                    _sleep(15 * (attempt + 1), self.cancelled)
            except requests.RequestException as e:
                err = str(e)[:120]
            _sleep(1.5 * (attempt + 1), self.cancelled)
        raise IOError("Google TTS မရပါ (%s)" % err)

    def synth(self, text, voice=None, rate_pct=0):
        parts = split_for_tts(_clean(text))
        if not parts:
            raise ValueError("TTS စာသား ပြောလို့မရပါ: '%s'" % text)
        return _join([decode_to_pcm(self._fetch(p), RATE) for p in parts], 0.12)
