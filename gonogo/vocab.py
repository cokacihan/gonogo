"""Words for matching requirement text against test code (`gonogo suggest`). Deterministic, no model download.

Test code side: identifiers of a function or TEST body, with comments and string literals already removed by the
importer, split at underscores and camelCase (`sendCmd_HLTH_ENABLE` -> send, command, health, enable).
Requirement side: the words of the SDD sentence without English filler.
Both sides go through the same normalisation (lower case, a few F´ abbreviations, a light suffix stripper), and
Relate's full-text index ranks the test code by BM25 against the requirement's words. Rare words such as `ping`
or `downlink` weigh more than words every test uses, without a hand-made stop list for C++ beyond the keywords.
"""
from __future__ import annotations

import re

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PIECE = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")
WORD = re.compile(r"[A-Za-z]+")

# F´ abbreviations seen in identifiers and SDD text; mapped to the word a requirement sentence uses
ABBREV = {"cmd": "command", "cmds": "command", "tlm": "telemetry", "evr": "event", "evt": "event", "prm": "parameter",
          "param": "parameter", "params": "parameter", "hlth": "health", "seq": "sequence", "buf": "buffer",
          "buff": "buffer", "chan": "channel", "msg": "message", "msgs": "message", "addr": "address",
          "len": "length", "init": "initialize", "config": "configuration", "cfg": "configuration",
          "num": "number", "max": "maximum", "min": "minimum", "err": "error", "dl": "downlink", "ul": "uplink"}

CPP = set("""auto bool break case catch char class const constexpr continue default delete double else enum explicit
extern false float for friend goto inline int long mutable namespace new noexcept nullptr operator override private
protected public register return short signed sizeof static struct switch template this throw true try typedef
typename union unsigned using virtual void volatile while std size_t uint int8 int16 int32 int64 uint8 uint16
uint32 uint64 u8 u16 u32 u64 i8 i16 i32 i64 f32 f64 fw requirement""".split())

STOP = set("""the and for with that this these those shall will must should may can are was were has have had been
being from into onto when then than each all any its not only such via per upon both either which while where what
who whose there their they them also other more most some same own one two use used uses using component components
provide provides provided support supports supported able allow allows""".split())


def norm(word: str) -> str:
    w = ABBREV.get(word.lower(), word.lower())
    for suffix in ("ing", "ed", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            w = w[: -len(suffix)]
            break
    return w[:-1] if w.endswith("e") and len(w) > 4 else w


def code_words(code: str) -> set[str]:
    out = set()
    for ident in IDENT.findall(code):
        if ident.lower() in CPP:
            continue
        for piece in PIECE.findall(ident):
            if len(piece) >= 3 and piece.lower() not in CPP:
                out.add(norm(piece))
            elif piece.lower() in ABBREV:  # "dl", "ul"
                out.add(norm(piece))
    return out


def text_words(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for w in WORD.findall(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)):
        if len(w) >= 3 and w.lower() not in STOP or w.lower() in ABBREV:
            seen.setdefault(norm(w), None)
    return list(seen)
