import re

class MockApp:
    def __init__(self):
        self.last_thought = ""

    def _extract_thought_from_stream(self, stream):
        """Helper to extract [THOUGHT] content and yield the remaining response."""
        buffer = ""
        in_thought = False
        self.last_thought = ""

        # Patterns for thought-start and thought-end (case-insensitive, handles brackets and parentheses)
        start_pattern = re.compile(r'\[THOUGHTS?\]|\(THOUGHTS?\)', re.IGNORECASE)
        end_pattern = re.compile(r'\[/THOUGHTS?\]|\(/THOUGHTS?\)', re.IGNORECASE)

        for chunk in stream:
            buffer += chunk
            while True:
                if not in_thought:
                    match = start_pattern.search(buffer)
                    if match:
                        # Yield everything BEFORE the tag
                        pre_tag = buffer[:match.start()]
                        if pre_tag:
                            yield pre_tag
                        buffer = buffer[match.end():]
                        in_thought = True
                        continue
                    else:
                        # No start tag found. Yield safe buffer, keeping enough to catch partial tags.
                        if len(buffer) > 15:
                            yield buffer[:-15]
                            buffer = buffer[-15:]
                        break
                else:
                    match = end_pattern.search(buffer)
                    if match:
                        # Collect the thought content
                        self.last_thought += buffer[:match.start()]
                        buffer = buffer[match.end():]
                        in_thought = False
                        continue
                    else:
                        # Inside thought, wait for closing tag.
                        # Buffer enough to handle partial tags.
                        if len(buffer) > 15:
                            self.last_thought += buffer[:-15]
                            buffer = buffer[-15:]

                        # Safety cap for thoughts (prevent infinite growth)
                        if len(self.last_thought) > 4000:
                            in_thought = False
                        break

        # Final cleanup
        if buffer:
            if in_thought:
                self.last_thought += buffer
                # If the stream ended without a closing tag, treat the content as response
                # especially if it's long and doesn't have an opening tag anymore.
                if len(self.last_thought) > 50 and not start_pattern.search(self.last_thought):
                    yield self.last_thought
                    self.last_thought = ""
            else:
                # Last resort check for bracketed thought if nothing was extracted
                if not self.last_thought and buffer.strip().startswith("[") and "]" in buffer:
                    match = re.match(r'^\[(.*?)\]', buffer.strip())
                    if match:
                        self.last_thought = match.group(1)
                        yield buffer.strip()[match.end():].strip()
                        return
                yield buffer

def test_extraction(stream_content, expected_response, expected_thought):
    app = MockApp()
    response = "".join(list(app._extract_thought_from_stream(stream_content)))
    print(f"Input: {stream_content}")
    print(f"Extracted Response: '{response}'")
    print(f"Extracted Thought: '{app.last_thought}'")
    assert response.strip() == expected_response.strip()
    assert app.last_thought.strip() == expected_thought.strip()
    print("SUCCESS\n")

if __name__ == "__main__":
    # Test 1: Normal Case
    test_extraction(["[THOUGHT] I like machines [/THOUGHT] Yah-hoh!"], "Yah-hoh!", "I like machines")

    # Test 2: Fragmented chunks
    test_extraction(["[THO", "UGHT] I c", "atch a", " whiff [/THOUGHT] Hi!"], "Hi!", "I catch a whiff")

    # Test 3: Missing closing tag, long content (should yield as response)
    test_extraction(["[THOUGHT] This is a very long response that forgot to close its thought tag but it is definitely meant for the user."],
                    "This is a very long response that forgot to close its thought tag but it is definitely meant for the user.", "")

    # Test 4: Missing closing tag, short content (stays as thought)
    test_extraction(["[THOUGHT] Short thought"], "", "Short thought")

    # Test 5: No thought tags
    test_extraction(["Hello Tyler!"], "Hello Tyler!", "")

    print("All tests passed!")
