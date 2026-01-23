from __future__ import annotations

from llm.llama_cpp_client import LlamaCppClient


class LlmScorer:
    def __init__(self, llm: LlamaCppClient) -> None:
        self._llm = llm

    def score(self, text: str, summary: str) -> float:
        if not text or not summary:
            return 0.0
        # quick heuristic: proportion of summary words appearing in text
        summary_words = {word.lower() for word in summary.split()}
        if not summary_words:
            return 0.0
        text_words = {word.lower() for word in text.split()}
        return len(summary_words & text_words) / len(summary_words)
