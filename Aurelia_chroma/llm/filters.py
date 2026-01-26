from __future__ import annotations
import re

class ContentFilter:
    """A simple content filter for Aurelia's inputs and outputs."""

    def __init__(self):
        # Example banned words/patterns. In a real scenario, this would be more extensive.
        self.banned_patterns = [
            re.compile(r"banned_word_1", re.IGNORECASE),
            re.compile(r"banned_word_2", re.IGNORECASE),
            # Add more patterns as needed
        ]

    def is_appropriate(self, text: str) -> bool:
        """Checks if the text contains any banned patterns."""
        for pattern in self.banned_patterns:
            if pattern.search(text):
                return False
        return True

    def filter_text(self, text: str) -> str:
        """Filters out banned patterns from the text."""
        filtered_text = text
        for pattern in self.banned_patterns:
            filtered_text = pattern.sub("[REDACTED]", filtered_text)
        return filtered_text
