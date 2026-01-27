from __future__ import annotations
import re

class ContentFilter:
    """A simple content filter for Aurelia's inputs and outputs."""

    def __init__(self):
        # Professional-grade content filter.
        # This can be expanded with more patterns to ensure stream safety.
        self.banned_patterns = [
            re.compile(r"nazi|hitler|holocaust", re.IGNORECASE),
            # Add more patterns as needed for toxicity and safety
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
