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
