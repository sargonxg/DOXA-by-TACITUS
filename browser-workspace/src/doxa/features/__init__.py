"""Portable DOXA feature transformations."""
from collections import Counter
from math import log, sqrt
from unicodedata import normalize

NGRAMS = (3, 4, 5)
WHITESPACE = " \t\n\r\v\f\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
FOLDING = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ" + WHITESPACE,
    "abcdefghijklmnopqrstuvwxyz" + " " * len(WHITESPACE),
)
LIMIT = 6000


def preprocess(value):
    folded = normalize("NFC", value).translate(FOLDING)
    return " ".join(part for part in folded.split(" ") if part)


def grams(value, channel):
    value = preprocess(value)
    return Counter(
        channel + ":" + value[i:i + n]
        for n in NGRAMS
        for i in range(len(value) - n + 1)
    )


def vector(text, context, vocabulary, idf):
    text_counts = grams(text, "t")
    context_counts = grams(context, "c")
    has_text = any(gram in vocabulary for gram in text_counts)
    counts = {
        vocabulary[gram]: count
        for gram, count in (text_counts | context_counts).items()
        if gram in vocabulary
    }
    weights = {
        idx: (1 + log(count)) * float(idf[idx])
        for idx, count in sorted(counts.items())
    }
    norm = sqrt(sum(weight * weight for weight in weights.values()))
    if norm:
        weights = {idx: weight / norm for idx, weight in weights.items()}
    return counts, weights, has_text


def get_spec():
    return {
        "feature_version": "char-tfidf-v1",
        "ngram_lengths": list(NGRAMS),
        "max_vocabulary": 15000,
        "channels": {"t": "text", "c": "context"},
        "context_weight": 1.0,
        "normalization": "l2_after_channel_concatenation",
        "unicode": "NFC, code-point n-grams, ASCII-only lowercase",
        "whitespace_codepoints": [ord(char) for char in WHITESPACE],
        "term_frequency": "1 + ln(count)",
        "idf": "ln((1 + N) / (1 + document_frequency)) + 1",
        "document_frequency": "Unique original (passage, ordered context text) training input identities. Each channel-prefixed gram contributes once per identity.",
        "vocabulary_order": "Descending document frequency, ascending Unicode code-point lexicographic order",
        "max_text_codepoints": LIMIT,
        "max_context_codepoints": LIMIT,
        "unknown_passage_policy": "abstain even if context matches",
    }
