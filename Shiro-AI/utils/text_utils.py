"""
text_utils.py — Shared text processing utilities for Shiro AI.

All regexes compiled at module level for hot-path performance.
Used by shiro_engine, memory, thought_loop, store, and inner_mind.
"""

from __future__ import annotations

import re
from typing import Iterator

# ── Compiled regexes ──────────────────────────────────────────────────────────

_DELIMITERS       = re.compile(r'([.!?\n])')
_PUNCT_NO_SPACE   = re.compile(r'([.!?,])([A-Za-z])')
_CAMEL_JOIN       = re.compile(r'([a-z])([A-Z])')
_CONTRACTION_JOIN = re.compile(r"([a-zA-Z])('(?:d|s|t|ve|re|ll|m|nt))([a-zA-Z])")
_BOLD_STRIP       = re.compile(r'\*{1,2}([^*\n]+)\*{1,2}')
_YAML_KEY         = re.compile(r'^[\w\d][\w\d\-_ "\']*:\s*', re.MULTILINE)
_NGRAM_CLEAN      = re.compile(r'[^a-z0-9 ]')
_WS_NORM          = re.compile(r'\s+')


def _repair_spaces(text: str) -> str:
    """Fix common LLM token-merge artefacts in streaming chunks."""
    text = _PUNCT_NO_SPACE.sub(r'\1 \2', text)
    text = _CAMEL_JOIN.sub(r'\1 \2', text)
    text = _CONTRACTION_JOIN.sub(r'\1\2 \3', text)
    return text


def split_into_sentences(text_stream: Iterator[str]) -> Iterator[str]:
    """
    Yield sentence fragments from a streaming LLM token iterator.
    Each fragment ends at .  !  ?  newline, or after ~12 words for low latency.
    Space repairs applied to each chunk before buffering.
    """
    buffer = ""
    for chunk in text_stream:
        buffer += _repair_spaces(chunk)
        while True:
            match = _DELIMITERS.search(buffer)
            if match:
                pos      = match.end()
                sentence = buffer[:pos].strip()
                buffer   = buffer[pos:]
                if sentence:
                    if sentence[-1] in '.!?' and not sentence.endswith('...'):
                        sentence += ' '
                    yield sentence
                continue
            words = buffer.split()
            if len(words) >= 12:
                last_space = buffer.rfind(' ')
                if last_space != -1:
                    fragment = buffer[:last_space].strip()
                    buffer   = buffer[last_space:].lstrip()
                    if fragment:
                        yield fragment + ' '
                else:
                    yield buffer.strip()
                    buffer = ''
            break
    remaining = buffer.strip()
    if remaining:
        yield remaining


def clean_yaml_block(text: str) -> str:
    """
    Extract and clean a valid YAML block from raw LLM output.

    Handles code fences, bold markers, column-0 bullet-star → dash conversion,
    leading prose, and trailing prose.  Does NOT re-indent already-indented
    list items (that was a bug in the previous version which broke nested YAML).
    """
    # Extract from markdown fence
    if '```' in text:
        parts = text.split('```')
        if len(parts) >= 3:
            candidate = parts[1]
            first_nl  = candidate.find('\n')
            if first_nl != -1:
                lang = candidate[:first_nl].strip().lower()
                if lang in ('yaml', 'yml', ''):
                    candidate = candidate[first_nl + 1:]
            text = candidate

    # Strip bold markers
    text = _BOLD_STRIP.sub(r'\1', text)

    # Convert column-0 bullet stars only
    lines = text.split('\n')
    lines = [('- ' + ln[2:]) if ln.startswith('* ') else ln for ln in lines]
    text  = '\n'.join(lines).strip()
    if not text:
        return text

    # Skip leading natural-language prose
    lines     = text.splitlines()
    start_idx = -1
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        if _YAML_KEY.match(s) or s.startswith('- '):
            start_idx = i
            break

    if start_idx == -1:
        return text

    # Collect YAML lines, stop at unindented non-YAML prose
    yaml_lines: list = []
    for ln in lines[start_idx:]:
        s = ln.strip()
        if not s:
            yaml_lines.append(ln)
            continue
        if (ln[0] in (' ', '\t')
                or s.startswith('- ')
                or _YAML_KEY.match(s)
                or s[0] in ('"', "'")):
            yaml_lines.append(ln)
        else:
            break

    return '\n'.join(yaml_lines).strip()


def get_text_ngrams(text: str, n: int = 3) -> set:
    """Character n-grams of cleaned text for similarity scoring."""
    t = _NGRAM_CLEAN.sub('', text.lower())
    t = _WS_NORM.sub(' ', t).strip()
    if len(t) < n:
        return set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def calculate_text_similarity(text_a: str, text_b: str, n: int = 3) -> float:
    """Jaccard similarity of character n-grams.  Returns 0.0–1.0."""
    ng_a = get_text_ngrams(text_a, n)
    ng_b = get_text_ngrams(text_b, n)
    if not ng_a or not ng_b:
        return 0.0
    union = len(ng_a | ng_b)
    return len(ng_a & ng_b) / union if union else 0.0
# ── Token estimation ──────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """
    Approximate token count: ~1 token per 3.8 chars (conservative).
    No external dependencies. Used by memory, inner_mind, context builders.
    """
    return max(1, int(len(text) / 3.8))