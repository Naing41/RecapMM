"""Gemini REST (direct to Google). Key rotation, 429 wait, model fallback. Keys never logged."""
import json
import re
import time

import requests

DEFAULT_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash",
                  "gemini-3.7-flash", "gemini-3.1-flash-lite"]
BASE = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"


class KeyPoolExhausted(RuntimeError):
    pass


class Stopped(Exception):
    pass


def redact(s):
    s = re.sub(r"(?i)(key=)[^&\s\"']+", r"\1***", s or "")
    s = re.sub(r"AQ\.[0-9A-Za-z_.\-~+/=]{8,}", "AQ.***", s)
    return re.sub(r"AIza[0-9A-Za-z_\-]{20,}", "AIza***", s)


def parse_json(raw):
    t = raw.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json|JSON)?", "", t).strip()
        if t.endswith("```"):
            t = t[:-3].strip()
    m = re.search(r"[{\[]", t)
    if not m:
        raise ValueError("JSON မတွေ့ပါ")
    obj, _ = json.JSONDecoder().raw_decode(t[m.start():])
    return obj


class Gemini:
    def __init__(self, keys, models, min_interval, log, cancelled):
        if not keys:
            raise RuntimeError("Gemini API key မထည့်ရသေးပါ — Settings မှာ ထည့်ပါ")
        self.keys, self.models = keys, models or DEFAULT_MODELS
        self.min_interval, self.log, self.cancelled = min_interval, log, cancelled
        self.mi = self.ki = 0
        self.dead, self.exhausted = set(), set()
        self.last = {}
        self.rate_waits = 0
        self.busy_rounds = 0

    def _sleep(self, sec):
        end = time.time() + sec
        while time.time() < end:
            if self.cancelled():
                raise Stopped()
            time.sleep(min(0.25, end - time.time()))

    def _usable(self, m, k):
        return k not in self.dead and (m, k) not in self.exhausted

    def _advance_key(self):
        for step in range(1, len(self.keys)):
            k = (self.ki + step) % len(self.keys)
            if self._usable(self.mi, k):
                self.ki = k
                return True
        return False

    def _advance_model(self, why):
        for m in range(self.mi + 1, len(self.models)):
            for k in range(len(self.keys)):
                if self._usable(m, k):
                    self.log("🔁 %s → model %s" % (why, self.models[m]))
                    self.mi, self.ki = m, k
                    return
        raise KeyPoolExhausted("Gemini key/model အကုန် ကန့်သတ်ချက်ပြည့်သွားပါပြီ — နောက်မှ Resume လုပ်ပါ (သို့) project မတူတဲ့ key အသစ်ထည့်ပါ။ ပြီးသမျှ သိမ်းထားပါတယ်။")

    def _advance_key_or_model(self, why):
        if self._advance_key():
            self.log("🔑 %s → နောက် key သုံးမယ်" % why)
        else:
            self._advance_model(why)

    def _advance_on_busy(self, why):
        for m in range(self.mi + 1, len(self.models)):
            for k in range(len(self.keys)):
                if self._usable(m, k):
                    self.log("🔁 %s → model %s" % (why, self.models[m]))
                    self.mi, self.ki = m, k
                    return
        self.busy_rounds += 1
        if self.busy_rounds > 5:
            raise KeyPoolExhausted("Gemini model အကုန် busy ဖြစ်နေတယ် — နောက်မှ ဆက်လုပ်ပါ။")
        wait = min(300, 30 * (1 << (self.busy_rounds - 1)))
        self.log("⏳ Gemini busy — %d စက္ကန့် စောင့်ပြီး ပြန်စမယ်" % wait)
        self._sleep(wait)
        self.mi = 0

    def _call(self, model, key, prompt, audio=None, frames=None):
        import base64
        parts = []
        if audio:
            parts.append({"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(audio).decode()}})
        parts.append({"text": prompt})
        for label, jpg in (frames or []):          # each picture announced by its movie timestamp
            parts.append({"text": label})
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(jpg).decode()}})
        payload = {"contents": [{"parts": parts}],
                   "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3}}
        r = requests.post(BASE % model, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                          data=json.dumps(payload), timeout=(30, 240))
        if r.status_code != 200:
            raise _Http(r.status_code, redact(r.text)[:800])
        root = r.json()
        cands = root.get("candidates") or []
        if not cands:
            raise IOError("Gemini ပြန်ဖြေချက် ဗလာ: " + redact(str(root.get("promptFeedback", "")))[:200])
        parts = (cands[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts)

    def json(self, prompt, retries=3, audio=None, frames=None):
        attempt, last = 1, None
        while attempt <= retries:
            if self.cancelled():
                raise Stopped()
            key, model = self.keys[self.ki], self.models[self.mi]
            wait = self.last.get(key, 0) + self.min_interval - time.time()
            if wait > 0:
                self._sleep(wait)
            self.last[key] = time.time()
            try:
                out = parse_json(self._call(model, key, prompt, audio, frames))
                self.rate_waits = 0
                if len(self.keys) > 1:
                    self._advance_key()
                return out
            except _Http as e:
                last = e
                body = e.body
                if e.code == 429:
                    if re.search(r"PerDay|per day|daily|limit:\s*0\b", body, re.I):
                        self.exhausted.add((self.mi, self.ki))
                        self.log("⚠️ %s နေ့စဉ် quota ပြည့်ပြီ (key %d)" % (model, self.ki + 1))
                        self._advance_key_or_model("daily quota")
                    else:
                        self.rate_waits += 1
                        if self.rate_waits >= 4:
                            self.rate_waits = 0
                            self.exhausted.add((self.mi, self.ki))
                            self._advance_key_or_model("rate limit ဆက်ဖြစ်နေ")
                        else:
                            m = re.search(r'retryDelay"\s*:\s*"([0-9.]+)s"|retry in ([0-9.]+)s', body, re.I)
                            d = float(next((g for g in (m.groups() if m else []) if g), 20)) + 2 + 3 * (time.time() % 1)
                            self.log("⏳ 429 — %d စက္ကန့် စောင့်မယ်" % d)
                            self._sleep(d)
                elif e.code in (401, 403) or (e.code == 400 and "API key" in body):
                    self.dead.add(self.ki)
                    self.log("⛔ key %d ကို Google က ငြင်းတယ် (%d)" % (self.ki + 1, e.code))
                    if len(self.dead) >= len(self.keys):
                        raise KeyPoolExhausted("Gemini key အကုန် မမှန်ပါ — Settings မှာ စစ်ပါ")
                    self._advance_key_or_model("key rejected")
                elif e.code == 404:
                    self._advance_model("model %s မရှိ" % model)
                elif e.code >= 500 or e.code == 408:
                    self._advance_on_busy("service error %d" % e.code)
                else:
                    self.log("⚠️ Gemini HTTP %d: %s" % (e.code, body[:200]))
                    attempt += 1
                    self._sleep(1.2 * attempt)
            except ValueError as e:
                last = e
                self.log("⚠️ Gemini JSON မမှန်ပါ (%d/%d)" % (attempt, retries))
                attempt += 1
                self._sleep(1.2 * attempt)
            except requests.Timeout as e:
                last = e
                self.log("⏱ Gemini timeout")
                self._advance_on_busy("timeout")
            except (requests.RequestException, IOError) as e:
                last = e
                self.log("⚠️ Network: %s (%d/%d) — အင်တာနက် (VPN) စစ်ပါ" % (redact(str(e))[:120], attempt, retries))
                attempt += 1
                self._sleep(2.5 * attempt)
        raise RuntimeError("Gemini %d ကြိမ်စမ်းပြီး မအောင်မြင်ပါ: %s" % (retries, redact(str(last))))


class _Http(Exception):
    def __init__(self, code, body):
        super().__init__("HTTP %d" % code)
        self.code, self.body = code, body
