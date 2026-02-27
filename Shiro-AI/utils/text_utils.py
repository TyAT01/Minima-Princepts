import re

def split_into_sentences(text_stream):
    """
    Yields sentence fragments from a stream of text chunks.
    Delimiters are . ! ? \n and , or after a certain word count for "instant" feel.
    """
    buffer = ""
    # Punctuation that usually ends a sentence or indicates a pause
    delimiters = re.compile(r'([.!?\n,])')

    for chunk in text_stream:
        buffer += chunk

        # Check if we have any delimiters in the buffer
        while True:
            match = delimiters.search(buffer)
            if match:
                pos = match.end()
                sentence = buffer[:pos].strip()
                if sentence:
                    yield sentence
                buffer = buffer[pos:]
                continue

            # If no delimiter, but buffer is getting long, yield by word count for "instant" feel
            words = buffer.split()
            if len(words) >= 8:
                # Find the last space to yield full words
                last_space = buffer.rfind(" ")
                if last_space != -1:
                    fragment = buffer[:last_space].strip()
                    if fragment:
                        yield fragment
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
