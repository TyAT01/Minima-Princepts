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
        # Added more variants to catch common model mistakes
        start_pattern = re.compile(r'\[THOUGHTS?\]|\(THOUGHTS?\)|\[INNER MONOLOGUE\]|\[THINKING\]', re.IGNORECASE)
        end_pattern = re.compile(r'\[/THOUGHTS?\]|\(/THOUGHTS?\)|\[/INNER MONOLOGUE\]|\[/THINKING\]', re.IGNORECASE)

        for chunk in stream:
            buffer += chunk
            # print(f"DEBUG: chunk loop, buffer='{buffer}', in_thought={in_thought}")
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
                        # [Refined] Before yielding safe buffer, check for simple bracketed thoughts at the start
                        if not self.last_thought and buffer.strip().startswith("[") and "]" in buffer:
                            # If we see "[...]" and it's not a known start tag (checked above)
                            # it might be a simple bracketed thought.
                            # We only do this if it's at the very start of the whole response.
                            start_idx = buffer.find("[")
                            closing_idx = buffer.find("]")
                            if start_idx < closing_idx:
                                self.last_thought = buffer[start_idx+1:closing_idx]
                                buffer = buffer[closing_idx+1:].lstrip()
                                continue

                        # No start tag found. Yield safe buffer, keeping enough to catch partial tags.
                        if len(buffer) > 25:
                            yield buffer[:-25]
                            buffer = buffer[-25:]
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
                        # Inside thought, wait for closing tag or stream end.
                        # Safety cap for thoughts (prevent infinite growth)
                        if len(buffer) + len(self.last_thought) > 4000:
                            self.last_thought += buffer
                            buffer = ""
                            in_thought = False
                        break

        # Final cleanup
        # print(f"DEBUG: final cleanup, buffer='{buffer}', in_thought={in_thought}, last_thought='{self.last_thought}'")
        if buffer:
            if in_thought:
                # If it ends while in thought, it might be an unclosed thought or a leaked response
                if len(buffer) > 100 or "." in buffer:
                     self.last_thought += " [Unclosed]"
                     yield buffer
                else:
                    self.last_thought += buffer
            else:
                # Last resort check for bracketed thought if nothing was extracted
                if not self.last_thought and buffer.strip().startswith("[") and "]" in buffer:
                    start_idx = buffer.find("[")
                    closing_idx = buffer.find("]")
                    if start_idx < closing_idx:
                        pre_bracket = buffer[:start_idx]
                        if pre_bracket:
                            yield pre_bracket
                        self.last_thought = buffer[start_idx+1:closing_idx]
                        yield buffer[closing_idx+1:].strip()
                        return
                yield buffer

def clean_response(fragment):
    # Clean up any leaked thought/action blocks completely
    clean_fragment = re.sub(r'\[(THOUGHT|INNER MONOLOGUE|THINKING|ACTION|SCENE|META|SYSTEM)\].*?\[/(THOUGHT|INNER MONOLOGUE|THINKING|ACTION|SCENE|META|SYSTEM)\]', '', fragment, flags=re.IGNORECASE | re.DOTALL)
    clean_fragment = re.sub(r'\(THOUGHT\).*?\(/THOUGHT\)', '', clean_fragment, flags=re.IGNORECASE | re.DOTALL)

    # Remove any remaining bracketed text or parentheticals
    clean_fragment = re.sub(r'\[.*?\](?!\()|(?<!\])\(.*?\)', '', clean_fragment).strip()
    return clean_fragment

def test_extraction(stream_content, expected_response, expected_thought):
    app = MockApp()
    response = "".join(list(app._extract_thought_from_stream(stream_content)))
    print(f"Input: {stream_content}")
    print(f"Extracted Response: '{response}'")
    print(f"Extracted Thought: '{app.last_thought}'")
    assert response.strip() == expected_response.strip()
    assert app.last_thought.strip() == expected_thought.strip()
    print("SUCCESS\n")

def test_cleaning(fragment, expected):
    cleaned = clean_response(fragment)
    print(f"Input: '{fragment}'")
    print(f"Cleaned: '{cleaned}'")
    assert cleaned.strip() == expected.strip()
    print("SUCCESS\n")

if __name__ == "__main__":
    print("--- Testing Extraction ---")
    # Test 1: Normal Case
    test_extraction(["[THOUGHT] I like machines [/THOUGHT] Yah-hoh!"], "Yah-hoh!", "I like machines")

    # Test 2: Fragmented chunks
    test_extraction(["[THO", "UGHT] I c", "atch a", " whiff [/THOUGHT] Hi!"], "Hi!", "I catch a whiff")

    # Test 3: Missing closing tag, long content (should yield as response)
    test_extraction(["[THOUGHT] This is a very long response that forgot to close its thought tag but it is definitely meant for the user. Here are some more sentences to be sure."],
                    "This is a very long response that forgot to close its thought tag but it is definitely meant for the user. Here are some more sentences to be sure.", " [Unclosed]")

    # Test 4: Missing closing tag, short content (stays as thought)
    test_extraction(["[THOUGHT] Short thought"], "", "Short thought")

    # Test 5: No thought tags
    test_extraction(["Hello Tyler!"], "Hello Tyler!", "")

    # Test 6: New variants
    test_extraction(["[INNER MONOLOGUE] I'm thinking. [/INNER MONOLOGUE] Hello!"], "Hello!", "I'm thinking.")
    test_extraction(["[THINKING] Hmm... [/THINKING] What's up?"], "What's up?", "Hmm...")

    # Test 7: Parentheses variant
    test_extraction(["(THOUGHT) Testing parens (/THOUGHT) Works?"], "Works?", "Testing parens")

    # Test 8: Bracketed thought at start without closing tag but with closing bracket
    test_extraction(["[Just a simple bracketed thought] The actual message."], "The actual message.", "Just a simple bracketed thought")

    # Test 9: Bracketed thought with leading whitespace
    test_extraction(["   [Thinking to myself] Response"], "Response", "Thinking to myself")

    print("--- Testing Cleaning ---")
    test_cleaning("Hello [THOUGHT] leaked [/THOUGHT] world", "Hello  world")
    test_cleaning("Check out [this link](http://example.com)", "Check out [this link](http://example.com)")
    test_cleaning("I am (very) happy", "I am  happy")
    test_cleaning("Look at [this] and [that]", "Look at  and")
    test_cleaning("[ACTION] waves [/ACTION] Hi!", "Hi!")

    print("All tests passed!")
