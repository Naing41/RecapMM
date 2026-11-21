"""Story-context pass + batched, validated translation to Burmese (port of Translator.kt, without the optional audit pass)."""
import json
import os
import re

from .gemini import Stopped, KeyPoolExhausted, redact
from .units import clean, timestamp

MAX_ITEMS, MAX_CHARS = 40, 24000
KEYS = ["characters", "relationships", "places", "terms", "plot_state", "name_consistency", "unresolved"]


def _src_lines(items):
    return "\n".join("[ID %05d] [%s] %s" % (s.id, timestamp(s), clean(s.text)) for s in items)


def _tr_lines(items):
    return "\n".join("[ID %05d] [%s] SOURCE: %s\nOUTPUT: %s" % (s.id, timestamp(s), clean(s.text), clean(s.translation)) for s in items)


def _batches(items):
    out, cur, chars = [], [], 0
    for s in items:
        ln = len(s.text) + 40
        if cur and (len(cur) >= MAX_ITEMS or chars + ln > MAX_CHARS):
            out.append(cur)
            cur, chars = [], 0
        cur.append(s)
        chars += ln
    if cur:
        out.append(cur)
    return out


def _speakable(s):
    return any(c.isalnum() for c in s)


def _foreign_letters(text):
    bare = re.sub(r"\([^()]*\)", "", text)
    bad = []
    for ch in bare:
        c = ord(ch)
        if ch.isalpha() and not (0x1000 <= c <= 0x109F or 0xA9E0 <= c <= 0xA9FF or 0xAA60 <= c <= 0xAA7F
                                 or c <= 0x024F or 0x1E00 <= c <= 0x1EFF):
            if ch not in bad:
                bad.append(ch)
    return bad


def _norm(s):
    return "".join(c for c in clean(s).lower() if c.isalnum())


class Translator:
    def __init__(self, ai, log, cancelled, source_lang="English", glossary=""):
        self.ai, self.log, self.cancelled = ai, log, cancelled
        self.source_lang, self.glossary = source_lang, glossary

    # ---------- story context ----------
    def story_context(self, segs, checkpoint, progress, initial=None):
        """initial = the memory left by the previous episode (names, relationships, plot so far), so a series stays consistent."""
        memory = {k: list((initial or {}).get(k) or [])[-60:] for k in KEYS}
        bs = _batches(segs)
        done = 0
        if os.path.isfile(checkpoint):
            try:
                saved = json.load(open(checkpoint, encoding="utf-8"))
                if saved.get("total") == len(bs):
                    done, memory = saved["completed"], saved["context"]
                    if done:
                        self.log("♻️ Story Context resume — %d/%d" % (done, len(bs)))
            except Exception:
                pass
        for idx, batch in enumerate(bs):
            i = idx + 1
            if i <= done:
                continue
            if self.cancelled():
                raise Stopped()
            prompt = f"""You are building CONTEXT MEMORY for a later translation task.
Do not translate the transcript now. Do not write narration. Do not summarize away named facts.
Read this next chronological source chunk and UPDATE the existing compact context memory.
The memory exists only to keep names, relationships, terminology, locations, identities,
and the current story state consistent when later translation requests are sent in separate API calls.

RULES:
- Preserve exact character/place/object names as they appear in the source when known.
- Keep important relationships, aliases, roles, reveals, and terminology.
- Keep plot_state chronological but compact. Do not invent anything.
- If an earlier assumption is corrected by this chunk, update it.
- Return JSON only with these keys:
  characters, relationships, places, terms, plot_state, name_consistency, unresolved
- Each key must contain a compact JSON array. Keep the total memory concise enough to reuse in every later request.

EXISTING MEMORY:
{json.dumps(memory, ensure_ascii=False)}

SOURCE CHUNK {i}/{len(bs)}:
{_src_lines(batch)}
"""
            data = self.ai.json(prompt)
            if not isinstance(data, dict):
                raise RuntimeError("Story context response must be a JSON object")
            memory = {k: data.get(k) if isinstance(data.get(k), list) else [] for k in KEYS}
            json.dump({"completed": i, "total": len(bs), "context": memory}, open(checkpoint, "w", encoding="utf-8"), ensure_ascii=False)
            self.log("🧠 Story Context %d/%d" % (i, len(bs)))
            progress(i / len(bs))
        return memory

    # ---------- translation ----------
    def _rules(self):
        sl = self.source_lang
        r10 = ""
        if sl != "English":
            src = "foreign-language" if sl == "Auto-detect" else sl
            r10 = ("10. The SOURCE is %s speech recognised automatically: it may contain small recognition errors; use the story "
                   "context to read it sensibly. Write every person/place name in Burmese script with ONE consistent transliteration "
                   "(never leave source-language characters or Latin romanization in the Burmese text), except well-known English brand names.\n11. " % src)
        gloss = ""
        if self.glossary.strip():
            gloss = ("\n\nFIXED TERMS - whenever one of these appears (in any form), always use exactly this Burmese, never another spelling:\n"
                     + self.glossary.strip()[:3000])
        return f"""You are a CONTEXT-AWARE BURMESE NARRATION TRANSLATOR.
Translate ONLY the CURRENT {sl} items into smooth, natural spoken Burmese that sounds as if it was originally written by a fluent Burmese movie/story narrator.

CORE PRINCIPLE:
Preserve the SOURCE MEANING and VISUAL EVENTS 100%, NOT the original wording or sentence structure.

NATIVE BURMESE NARRATOR STYLE:
- Use natural spoken Burmese narrator phrasing, rhythm, and connectors.
- Avoid stiff word-for-word translation; restructure wording and clause order INSIDE THE SAME ID when needed.
- Keep Burmese pronouns, kinship terms, honorifics, gendered self-reference, names, and relationships contextually correct.
- Keep dialogue intent and social register natural without turning ordinary speech into formal literary Burmese.

HARD RULES:
1. Do NOT remove, condense away, or omit meaningful source information. Every action, event, object, person, relationship, cause/effect, dialogue, and important visual detail must remain in the same ID.
2. Do NOT translate word-for-word when it sounds stiff or machine-like. You MAY restructure wording and clause order INSIDE THE SAME ID.
3. Small expansion is allowed only for clarity/natural flow; never exaggerate or add unrelated filler.
4. Do not invent plot events, opinions, hooks, intros, outros, or facts.
5. Keep every CURRENT ID exactly once, in order. Do not merge or split IDs.
6. Context is READ-ONLY. Never copy events from previous/next context into a current ID.
7. Maintain consistent names, relationships, places, objects, and terminology.
8. Timestamps are local metadata. Return no timestamps.
9. Return JSON only with top-level key "items". Each item must contain integer "id" and non-empty string "translation".
{r10}NEVER return placeholders such as "...", "…", dashes, empty text, or schema/example text. Every spoken SOURCE row must produce actual target-language narration words.{gloss}"""

    def _validate(self, current, payload):
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("Translation response must contain an items array")
        by_id, order = {}, []
        for o in items:
            if not isinstance(o, dict) or "id" not in o:
                continue
            try:
                i = int(o["id"])
            except (TypeError, ValueError):
                continue
            if i in by_id:
                raise ValueError("Duplicate translation ID: %d" % i)
            tr = clean(str(o.get("translation", "")))
            if not tr:
                raise ValueError("Empty translation ID: %d" % i)
            src = next((s for s in current if s.id == i), None)
            if src and _speakable(src.text) and not _speakable(tr):
                raise ValueError("Translation ID %d contains no speakable words (got '%s')" % (i, tr))
            by_id[i] = tr
            order.append(i)
        expected = [s.id for s in current]
        if order != expected:
            raise ValueError("ID mismatch. expected=%s..%s got=%s..%s" % (expected[:3], expected[-3:], order[:3], order[-3:]))
        return [by_id[s.id] for s in current]

    def _check_dups(self, prev, current, out):
        last_src = _norm(prev[-1].text) if prev else None
        last_tr = _norm(prev[-1].translation) if prev else None
        for s, o in zip(current, out):
            a, b = _norm(s.text), _norm(o)
            if last_tr is not None and len(clean(o)) >= 14 and b == last_tr and a != last_src:
                raise ValueError("ID %d repeats the previous line's translation" % s.id)
            last_src, last_tr = a, b

    def _translate_batch(self, memory, prev, current, nxt):
        ids = json.dumps([s.id for s in current])
        prompt = f"""{self._rules()}

STORY CONTEXT MEMORY:
{json.dumps(memory, ensure_ascii=False)}

PREVIOUS CONTEXT (READ-ONLY):
{_tr_lines(prev) if prev else "(none)"}

CURRENT ITEMS — CONVERT ALL OF THESE, ONE-FOR-ONE:
{_src_lines(current)}

NEXT CONTEXT (READ-ONLY LOOK-AHEAD):
{_src_lines(nxt) if nxt else "(none)"}

CRITICAL OUTPUT ID PROTOCOL:
- CURRENT IDs are immutable SOURCE IDs, not row positions.
- Return exactly {len(current)} items.
- Copy this exact ID sequence without dropping, renumbering, or inventing any ID:
{ids}
- Before responding, verify that items[].id exactly equals that sequence.
"""
        last = None
        for attempt in range(1, 4):
            try:
                payload = self.ai.json(prompt, retries=1)
                out = self._validate(current, payload)
                self._check_dups(prev, current, out)
                stray = [s.id for s, o in zip(current, out) if _foreign_letters(o)]
                if stray and attempt < 3:
                    raise ValueError("letters of another script inside the Burmese (IDs %s); write every word in Burmese script" % stray)
                return out
            except (KeyPoolExhausted, Stopped):
                raise
            except Exception as e:
                last = e
                self.log("⚠️ Translation validation %d/3: %s" % (attempt, redact(str(e))[:200]))
                prompt += ("\n\nCORRECTION REQUIRED: Previous response was invalid because: %s. Return the full CURRENT batch again with exactly %d items. "
                           "The exact items[].id sequence MUST be %s. Do not omit the final ID, renumber rows, or merge adjacent IDs." % (e, len(current), ids))
        if len(current) > 6:
            mid = len(current) // 2
            left, right = current[:mid], current[mid:]
            self.log("↪️ ID %d–%d ကို %d+%d ခွဲပြီး ပြန်မေးမယ်" % (current[0].id, current[-1].id, len(left), len(right)))
            limit = max(1, len(prev) or 6)
            lo = self._translate_batch(memory, prev[-limit:], left, right + nxt)
            for s, t in zip(left, lo):
                s.translation = t
            ro = self._translate_batch(memory, (prev + left)[-limit:], right, nxt)
            return lo + ro
        raise RuntimeError("Translation batch could not be validated: %s" % last)

    def translate_all(self, segs, memory, checkpoint, progress):
        saved = {}
        if os.path.isfile(checkpoint):
            try:
                saved = json.load(open(checkpoint, encoding="utf-8"))
            except Exception:
                saved = {}
        for s in segs:
            if saved.get(str(s.id)):
                s.translation = saved[str(s.id)]
        bs = _batches(segs)
        for bi, batch in enumerate(bs):
            if all(s.translation for s in batch):
                continue
            if self.cancelled():
                raise Stopped()
            st = segs.index(batch[0])
            prev = [s for s in segs[max(0, st - 6):st] if s.translation]
            en = st + len(batch)
            out = self._translate_batch(memory, prev, batch, segs[en:en + 6])
            for s, t in zip(batch, out):
                s.translation = t
            for s in segs:
                if s.translation:
                    saved[str(s.id)] = s.translation
            json.dump(saved, open(checkpoint, "w", encoding="utf-8"), ensure_ascii=False)
            n = sum(1 for s in segs if s.translation)
            self.log("📝 ဘာသာပြန် %d/%d (%d/%d)" % (bi + 1, len(bs), n, len(segs)))
            progress(n / len(segs))
        for s in segs:
            bad = _foreign_letters(s.translation)
            if bad:
                s.translation = clean("".join(c for c in s.translation if c not in bad))
                self.log("⚠️ ID %05d: တခြားဘာသာ စာလုံး ဖယ်ထားတယ် (%s) — စစ်ကြည့်ပါ" % (s.id, " ".join(bad)))
        progress(1.0)
