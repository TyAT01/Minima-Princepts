from __future__ import annotations


class MemoryDigestor:
    """Create a simple summary of user input for memory indexing."""

    def digest(self, text: str) -> str:
        words = text.strip().split()
        if len(words) <= 12:
            return text
        return " ".join(words[:12]) + "..."
