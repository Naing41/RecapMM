"""Sync units (port of Units.kt): sentence/clause/pause boundaries, ~4.2 s target, 5.6 s hard cap."""
import re
from dataclasses import dataclass

ABBR = {"mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "vs.", "etc.", "no.", "mt.",
        "inc.", "ltd.", "co.", "u.s.", "u.k.", "a.m.", "p.m.", "e.g.", "i.e."}
SENT_END = re.compile(r"[.!?…။।。！？][\"'’”)]?$")
SOFT_END = re.compile(r"[;,:၊；，、][\"'’”)]?$")
LONE_INITIAL = re.compile(r"[a-z]\.['’\"”)]?")
WS = re.compile(r"\s+")


@dataclass
class Word:
    text: str
    start: float
    end: float


class Seg:
    def __init__(self, id, start, end, text):
        self.id = id
        self.start = start
        self.end = end
        self.text = text
        self.vstart = 0.0
        self.vend = 0.0
        self.translation = ""


def clean(s):
    return WS.sub(" ", s or "").strip()


def _cjk(c):
    o = ord(c)
    return (0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF)


def join_words(ws):
    out = ""
    for w in ws:
        if not w:
            continue
        if out and not (_cjk(out[-1]) or _cjk(w[0])):
            out += " "
        out += w
    return clean(out)


def fmt_ts(sec):
    ms = round(sec * 1000)
    h, m, s = ms // 3600000, (ms // 60000) % 60, (ms // 1000) % 60
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms % 1000)


def timestamp(s):
    return "%s --> %s" % (fmt_ts(s.start), fmt_ts(s.end))


def _ends_sentence(text):
    c = clean(text).lower()
    if c in ABBR or LONE_INITIAL.fullmatch(c):
        return False
    return bool(SENT_END.search(c))


def _comparable(s):
    return "".join(ch for ch in clean(s).lower() if ch.isalnum())


def atomicize(words, target=4.2, max_dur=5.6):
    tgt = max(2.5, target)
    mx = max(tgt + 0.5, max_dur)
    min_natural = min(1.8, tgt * 0.4)
    raws, cur = [], []

    def close():
        nonlocal cur
        if cur:
            raws.append([cur[0].start, cur[-1].end, join_words([w.text for w in cur])])
            cur = []

    for i, w in enumerate(words):
        cur.append(w)
        dur = cur[-1].end - cur[0].start
        gap = max(0.0, words[i + 1].start - w.end) if i + 1 < len(words) else 0.0
        if gap >= 1.0: close()
        elif _ends_sentence(w.text): close()
        elif SOFT_END.search(clean(w.text)) and dur >= min_natural: close()
        elif dur >= min_natural and gap >= 0.50: close()
        elif dur >= tgt and gap >= 0.16: close()
        elif dur >= mx: close()
    close()

    merged, i = [], 0
    while i < len(raws):
        u = raws[i]
        dur = u[1] - u[0]
        tokens = len([t for t in u[2].split(" ") if t])
        gap = raws[i + 1][0] - u[1] if i + 1 < len(raws) else 9999.0
        if dur <= 0.5 and tokens <= 2 and i + 1 < len(raws) and gap <= 0.60:
            nx = raws[i + 1]
            first = nx[2].split(" ")[0] if nx[2] else ""
            left = _comparable(u[2].rstrip(".!?…"))
            if not (left and left == _comparable(first)):
                nx[2] = join_words([u[2], nx[2]])
            nx[0] = u[0]
            i += 1
            continue
        merged.append(u)
        i += 1
    return [Seg(k + 1, r[0], r[1], r[2]) for k, r in enumerate(merged)]


def drop_loops(segs):
    out, run, last = [], 0, ""
    for s in segs:
        k = _comparable(s.text)
        if not k:
            continue
        if k == last:
            run += 1
        else:
            run, last = 1, k
        if run <= 2:
            out.append(s)
    return [Seg(i + 1, s.start, s.end, s.text) for i, s in enumerate(out)]


def assign_coverage(segs, video_duration):
    """Every unit owns a contiguous slice of the picture; gaps are split at their middle."""
    if not segs:
        return
    segs[0].vstart = 0.0
    for i in range(len(segs) - 1):
        le, rs = segs[i].end, segs[i + 1].start
        b = (le + rs) / 2.0 if rs >= le else max(le, rs)
        b = min(max(b, 0.0), video_duration)
        segs[i].vend = b
        segs[i + 1].vstart = b
    segs[-1].vend = video_duration
