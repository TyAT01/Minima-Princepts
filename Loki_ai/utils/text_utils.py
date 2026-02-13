import re

def split_into_sentences(text_stream):
    """
    Yields sentence fragments from a stream of text chunks.
    Delimiters are . ! ? and \n
    """
    buffer = ""
    # Punctuation that usually ends a sentence
    sentence_endings = re.compile(r'([.!?\n])')

    for chunk in text_stream:
        buffer += chunk

        # Check if we have any sentence endings in the buffer
        while True:
            match = sentence_endings.search(buffer)
            if not match:
                break

            pos = match.end()
            sentence = buffer[:pos].strip()
            if sentence:
                yield sentence
            buffer = buffer[pos:]

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
        # Inclusive regex for YAML keys: allows spaces, dashes, underscores
        if re.match(r'^[ \w\d_-]+:\s*', stripped) or stripped.startswith('- '):
            start_idx = i
            break

    if start_idx != -1:
        text = "\n".join(lines[start_idx:])

    return text.strip()
