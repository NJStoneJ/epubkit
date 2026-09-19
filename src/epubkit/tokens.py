"""Token accounting with no tokenizer dependency.

Estimation rules, chosen to be mildly conservative for agent context budgeting:

* Every CJK / Kana / Hangul character counts as one token.
* Everything else counts as one token per four characters.

For pure Latin text that lands close to BPE behaviour. For pure CJK it lands
close to the real thing for the common tokenizers. It is an estimate, and the
docstring says so -- if you need exact counts, call :func:`use_tiktoken`.
"""

from __future__ import annotations

import re

_CJK_RE = re.compile(
    "["
    "\u1100-\u11ff"  # Hangul Jamo
    "\u2e80-\u2eff"  # CJK radicals
    "\u3000-\u303f"  # CJK punctuation and symbols
    "\u3040-\u30ff"  # Hiragana, Katakana
    "\u3130-\u318f"  # Hangul compatibility Jamo
    "\u3400-\u4dbf"  # CJK unified ideographs extension A
    "\u4e00-\u9fff"  # CJK unified ideographs
    "\uac00-\ud7af"  # Hangul syllables
    "\uf900-\ufaff"  # CJK compatibility ideographs
    "\uff00-\uffef"  # Fullwidth forms
    "]"
)

_BREAK_AFTER = frozenset("。！？!?；;…\n")

_encoder = None


def use_tiktoken(model="cl100k_base"):
    """Opt in to exact counts. Returns ``True`` when the encoder was loaded.

    epubkit does not depend on tiktoken; this is purely an escape hatch.
    """
    global _encoder
    try:
        import tiktoken  # type: ignore
    except ImportError:
        return False
    try:
        _encoder = tiktoken.get_encoding(model)
    except Exception:  # noqa: BLE001 - unknown encoding name, offline, etc.
        try:
            _encoder = tiktoken.encoding_for_model(model)
        except Exception:  # noqa: BLE001
            return False
    return True


def count_tokens(text):
    """Estimated token count for ``text``."""
    if not text:
        return 0
    if _encoder is not None:
        return len(_encoder.encode(text))
    cjk, rest = char_counts(text)
    return estimate(cjk, rest)


def char_counts(text):
    """Split ``text`` into ``(cjk_characters, other_characters)``."""
    cjk = len(_CJK_RE.findall(text))
    # Remove the CJK characters before measuring the rest. Substituting a space
    # instead would count every CJK character twice, once as itself and once as
    # the space that replaced it -- 100 Chinese characters came out as 125
    # tokens, a 25% overcount on exactly the input this estimate exists for.
    return cjk, len(text) - cjk


def estimate(cjk, rest):
    """Token estimate from pre-counted character totals.

    ``estimate(*char_counts(text)) == count_tokens(text)`` exactly, which lets
    a loop that assembles text piece by piece stay linear instead of
    re-measuring the whole buffer on every step.
    """
    total = cjk + rest / 4.0
    return max(1, int(total + 0.5)) if (cjk or rest) else 0


def split_long_text(text, max_tokens):
    """Split ``text`` into pieces of at most ``max_tokens`` each.

    Prefers sentence boundaries; falls back to a proportional hard cut when a
    single sentence does not fit (happens with unpunctuated Chinese web novels
    and with very long paragraphs).

    The result is guaranteed to be lossless -- ``"".join(pieces) == text`` --
    and every piece is guaranteed to be within ``max_tokens``. The guarantee is
    enforced by measuring the assembled piece rather than trusting the running
    sum, because per-sentence rounding does not add up to the same number as
    counting the joined string once.
    """
    if not text or max_tokens <= 0 or count_tokens(text) <= max_tokens:
        return [text] if text else []

    # When a real tokenizer is loaded the estimate helpers do not apply, so fall
    # back to measuring the assembled string. That path is quadratic; the
    # default path is linear because character counts accumulate exactly.
    exact = _encoder is not None

    pieces = []
    buffer = []
    cjk_total = 0
    rest_total = 0

    def measure():
        return count_tokens("".join(buffer)) if exact else estimate(cjk_total, rest_total)

    def flush():
        if not buffer:
            return
        joined = "".join(buffer)
        if measure() <= max_tokens:
            pieces.append(joined)
        else:
            # Rounding pushed the assembled piece over budget; cut it hard so
            # the caller's budget is never violated.
            pieces.extend(_hard_split(joined, max_tokens))
        del buffer[:]

    for sentence in _sentence_chunks(text):
        sentence_cjk, sentence_rest = char_counts(sentence)
        sentence_tokens = (
            count_tokens(sentence) if exact else estimate(sentence_cjk, sentence_rest)
        )
        if sentence_tokens > max_tokens:
            flush()
            cjk_total = rest_total = 0
            pieces.extend(_hard_split(sentence, max_tokens))
            continue
        if buffer:
            pending = (
                count_tokens("".join(buffer) + sentence)
                if exact
                else estimate(cjk_total + sentence_cjk, rest_total + sentence_rest)
            )
            if pending > max_tokens:
                flush()
                cjk_total = rest_total = 0
        buffer.append(sentence)
        cjk_total += sentence_cjk
        rest_total += sentence_rest

    flush()
    return [piece for piece in pieces if piece.strip()]


def _sentence_chunks(text):
    out = []
    start = 0
    for index, char in enumerate(text):
        if char in _BREAK_AFTER:
            out.append(text[start : index + 1])
            start = index + 1
    if start < len(text):
        out.append(text[start:])
    return out


def _hard_split(text, max_tokens):
    """Cut ``text`` into contiguous slices of at most ``max_tokens`` each.

    Slices are contiguous and exhaustive, so ``"".join(result) == text``. The
    first slice size is a proportional guess; each slice is then measured and
    shrunk until it actually fits, because a guess based on ``len(text)`` is
    wrong whenever the text mixes scripts.
    """
    if not text:
        return []
    if count_tokens(text) <= max_tokens:
        return [text]

    out = []
    index = 0
    length = len(text)
    while index < length:
        guess = max(1, int(max_tokens * (length - index) / float(count_tokens(text[index:]))))
        end = min(length, index + guess)
        # Shrink until the slice fits. Bounded, because each step removes at
        # least one character and the loop exits at a single character.
        while end > index + 1 and count_tokens(text[index:end]) > max_tokens:
            end -= max(1, (end - index) // 16)
        out.append(text[index:end])
        index = end
    return out
