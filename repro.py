import re
import sys
from typing import Generator

THOUGHT_KEYWORDS = "THOUGHTS?|INNER MONOLOGUE|INNER MIND|SHIRO|THINKING|PLOT|SCHEME|SCHEEM|META|SYSTEM|ACTION|SCENE|LOG|MOMENTUM|REL:|VALENCE"

def split_into_sentences(text_stream):
    buffer = ""
    delimiters = re.compile(r'([.!?\n,])')
    for chunk in text_stream:
        buffer += chunk
        while True:
            match = delimiters.search(buffer)
            if match:
                pos = match.end()
                sentence = buffer[:pos].strip()
                if sentence:
                    yield sentence
                buffer = buffer[pos:]
                continue
            words = buffer.split()
            if len(words) >= 6:
                last_space = buffer.rfind(" ")
                if last_space != -1:
                    fragment = buffer[:last_space].strip()
                    if fragment:
                        yield fragment
                    buffer = buffer[last_space:].lstrip()
                else:
                    yield buffer.strip()
                    buffer = ""
            break
    remaining = buffer.strip()
    if remaining:
        yield remaining

def _extract_thought_from_stream(stream):
    buffer = ""
    in_thought = False
    last_thought = ""
    start_pattern = re.compile(rf'\[(?:{THOUGHT_KEYWORDS})[^\]]*\]|\((?:{THOUGHT_KEYWORDS})[^\)]*\)|(?<!\w)THOUGHTS?:|<THOUGHTS?>|\*(?:Shiro\s+)?(?:THOUGHTS?|THINKING|SCHEMING|PLOTTING|THINKS?).*?\*', re.IGNORECASE)
    end_pattern = re.compile(rf'\[/(?:{THOUGHT_KEYWORDS})\]|\(/(?:{THOUGHT_KEYWORDS})\)|</THOUGHTS?>|\*(?:/THOUGHTS?|END THINKING|END SCHEMING|END|/|THOUGHTS?)\*', re.IGNORECASE)

    for chunk in stream:
        buffer += chunk
        while True:
            if not in_thought:
                match = start_pattern.search(buffer)
                if match:
                    pre_tag = buffer[:match.start()]
                    if pre_tag: yield pre_tag
                    tag_content = match.group(0)
                    buffer = buffer[match.end():].lstrip()
                    is_block_start = False
                    inner_text = re.sub(r'[\[\]\(\)\<\>\*]', '', tag_content).strip()
                    if any(re.fullmatch(k, inner_text, re.IGNORECASE) for k in THOUGHT_KEYWORDS.split('|')):
                        is_block_start = True

                    if is_block_start:
                        in_thought = True
                    else:
                        last_thought += tag_content + " "
                        if any(kw in tag_content.upper() for kw in ["SHIRO", "INNER", "MIND"]):
                            line_end = buffer.find("\n")
                            if line_end != -1:
                                last_thought += buffer[:line_end]
                                buffer = buffer[line_end:].lstrip()
                    continue
                else:
                    if len(buffer) > 40:
                        yield buffer[:-40]
                        buffer = buffer[-40:]
                    break
            else:
                match = end_pattern.search(buffer)
                if match:
                    last_thought += buffer[:match.start()]
                    buffer = buffer[match.end():].lstrip()
                    in_thought = False
                    continue
                else:
                    if "\n" in buffer:
                        parts = buffer.split("\n", 1)
                        if len(parts[1]) > 5 and parts[1].strip() and parts[1].strip()[0].isupper():
                            last_thought += parts[0]
                            buffer = parts[1]
                            in_thought = False
                            continue
                    break
    if buffer:
        yield buffer

def _clean_response(text: str) -> str:
    clean = re.sub(
        rf'\[(?:{THOUGHT_KEYWORDS})[^\]]*\].*?(?=\n|[A-Z]\w+|(?<!\|)\*|$)',
        '', text, flags=re.IGNORECASE | re.DOTALL
    )
    clean = re.sub(
        rf'\[(?:{THOUGHT_KEYWORDS})[^\]]*\].*?\[/(?:{self.THOUGHT_KEYWORDS})\]',
        '', clean, flags=re.IGNORECASE | re.DOTALL
    )
    # ... Simplified for repro
    return clean.strip()

test_input = "[SHIRO INNER MIND v4 -- turn 35 -- 18h 17m 23s] neutral [V:+0. 42 a:+0. 58 d:+0. 28] | momentum: stable accept & counter-attack | rel: PRIMARY USER | Hello there!"

print("Testing _extract_thought_from_stream:")
stream = [test_input[i:i+5] for i in range(0, len(test_input), 5)]
extracted = list(_extract_thought_from_stream(stream))
print(f"Extracted: {extracted}")

print("\nTesting _clean_response on each fragment:")
for fragment in split_into_sentences(iter(extracted)):
    cleaned = re.sub(
        rf'\[(?:{THOUGHT_KEYWORDS})[^\]]*\].*?(?=\n|[A-Z]\w+|(?<!\|)\*|$)',
        '', fragment, flags=re.IGNORECASE | re.DOTALL
    )
    print(f"Fragment: '{fragment}' -> Cleaned: '{cleaned}'")
