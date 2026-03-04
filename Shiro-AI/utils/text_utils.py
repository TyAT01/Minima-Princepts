import re

# Compiled once at module level — used by split_into_sentences hot path
_DELIMITERS = re.compile(r'([.!?\n])')
# Catches missing space after punctuation: "word.Word" → "word. Word"
_PUNCT_NO_SPACE = re.compile(r'([.!?,])([A-Za-z])')
# Catches tokens merged without space by the LLM tokeniser:
# "heytyler" would need a word-list; instead catch [a-z][A-Z] (CamelCase joins)
# and proper-noun runs after sentence terminals.
_CAMEL_JOIN = re.compile(r'([a-z])([A-Z])')
# Catches missing space after apostrophe-contractions: "i'mgoing" -> "i'm going"
_CONTRACTION_JOIN = re.compile(r"([a-zA-Z])('(?:d|s|t|ve|re|ll|m|nt))([a-zA-Z])")


def _repair_spaces(text: str) -> str:
    """
    Repair common LLM token-merge artefacts:
    1. Missing space after sentence punctuation: "hello.how" → "hello. how"
    2. CamelCase merges from adjacent tokens: "heyTyler" → "hey Tyler"
    3. Missing space after contractions: "i'mgoing" → "i'm going"
    Neither regex touches ellipses or standalone single-quoted words.
    """
    text = _PUNCT_NO_SPACE.sub(r'\1 \2', text)
    text = _CAMEL_JOIN.sub(r'\1 \2', text)
    text = _CONTRACTION_JOIN.sub(r'\1\2 \3', text)
    return text


def split_into_sentences(text_stream):
    """
    Yields sentence fragments from a streaming LLM token iterator.
    Each yielded fragment ends at a natural sentence boundary (. ! ? \n)
    or after ~12 words (so short responses appear immediately).
    Trailing space is appended so callers can safely use "".join().
    Token-merge space repairs are applied to each raw chunk before buffering.
    """
    buffer = ""

    for chunk in text_stream:
        # Apply safe space repairs to each token before buffering.
        # ONLY fixes punctuation→letter and CamelCase joins —
        # do NOT insert spaces between adjacent alpha tokens because
        # Ollama splits words mid-token ("Ident"+"ify") and inserting
        # a space there produces "Ident ify" which is worse.
        chunk = _repair_spaces(chunk)
        buffer += chunk

        # Flush complete sentences
        while True:
            match = _DELIMITERS.search(buffer)
            if match:
                pos = match.end()
                sentence = buffer[:pos].strip()
                if sentence:
                    # Ensure caller "".join produces "Sentence one. Sentence two."
                    if sentence[-1] in '.!?' and not sentence.endswith('...'):
                        sentence += ' '
                    yield sentence
                buffer = buffer[pos:]
                continue

            # No delimiter yet — flush by word count for low-latency feel.
            # 12 words keeps tsundere short-phrases intact before the . arrives.
            words = buffer.split()
            if len(words) >= 12:
                last_space = buffer.rfind(" ")
                if last_space != -1:
                    fragment = buffer[:last_space].strip()
                    if fragment:
                        yield fragment + " "
                    buffer = buffer[last_space:].lstrip()
                else:
                    # Fallback if no space (unlikely with 8 words)
                    yield buffer.strip()
                    buffer = ""
            break

    # Yield remaining buffer if any
    remaining = buffer.strip()
    if remaining:
        yield remaining

def clean_yaml_block(text: str) -> str:
    """
    Cleans up LLM output to extract a valid YAML block.
    Removes markdown formatting like bolding and triple backticks.
    Skips leading natural language text.
    """
    # 1. Extract from code blocks if present
    if "```yaml" in text:
        text = text.split("```yaml")[1].split("```")[0]
    elif "```yml" in text:
        text = text.split("```yml")[1].split("```")[0]
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            # Typical case: text ``` content ``` text
            text = parts[1]
            lines = text.strip().splitlines()
            if lines and lines[0].strip().lower() in ["yaml", "yml"]:
                text = "\n".join(lines[1:])

    # 2. Remove bolding (frequent source of YAML parse errors like **ANALYSIS**)
    text = text.replace("**", "")

    # 2b. Escape/Replace bullet points that might be confused with YAML aliases if not properly indented
    # (e.g., "* Fact" -> "Fact")
    text = re.sub(r'^\s*\* ', '  - ', text, flags=re.MULTILINE)
    # Ensure nested list items have leading spaces to be valid YAML
    text = re.sub(r'^-\s+', '  - ', text, flags=re.MULTILINE)

    # 3. Strip leading/trailing whitespace
    text = text.strip()

    # 4. Skip leading natural language (find first actual YAML key or list item)
    lines = text.splitlines()
    start_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Inclusive regex for YAML keys: allows spaces, dashes, underscores, and quotes
        if re.match(r'^[ \w\d\-_"\']+:\s*', stripped) or stripped.startswith('- '):
            start_idx = i
            break

    if start_idx != -1:
        # Keep only lines that appear to be part of the YAML block
        yaml_lines = []
        for line in lines[start_idx:]:
            # If we encounter a line that is NOT indented AND not a YAML key/list,
            # it might be the start of trailing natural language chatter.
            stripped = line.strip()
            if not stripped:
                yaml_lines.append(line)
                continue

            # If it's zero-indented but doesn't look like YAML, we stop.
            # (Allows for lines starting with space/tab, or starting with '-' or key:)
            if not line.startswith((' ', '\t', '-', '"', "'")) and ':' not in line.split('#')[0]:
                 # Potential start of non-YAML text
                 break
            yaml_lines.append(line)
        text = "\n".join(yaml_lines)

    return text.strip()


def get_text_ngrams(text: str, n: int = 3) -> set[str]:
    """Character n-grams of cleaned text."""
    t = re.sub(r"[^a-z0-9 ]", "", text.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return {t[i:i+n] for i in range(len(t) - n + 1)} if len(t) >= n else set()


def calculate_text_similarity(text_a: str, text_b: str, n: int = 3) -> float:
    """Jaccard similarity between character n-grams of two strings."""
    ng_a = get_text_ngrams(text_a, n)
    ng_b = get_text_ngrams(text_b, n)
    if not ng_a or not ng_b:
        return 0.0
    return len(ng_a & ng_b) / len(ng_a | ng_b)
