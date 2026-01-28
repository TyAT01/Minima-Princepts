from __future__ import annotations
import logging
import asyncio
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class Reflector:
    def __init__(self, chroma_client, memory_store):
        self.chroma_client = chroma_client
        self.memory_store = memory_store

    async def reflect_on_recent_interactions(self):
        """Analyzes recent short-term buffer and extracts a high-level lesson."""
        logger.info("Aurelia is reflecting on recent interactions...")

        short_term_context = self.memory_store.get_short_term_context()
        if "No recent interactions" in short_term_context:
            logger.info("Not enough context for reflection.")
            return None

        prompt = (
            "Review your recent REAL-WORLD interactions below. These are actual conversations with people in your community.\n"
            "Summarize what you've learned about the people you've talked to, your own reactions, or how you can be a better companion to them.\n\n"
            f"{short_term_context}\n\n"
            "Provide a concise 'Insight' that will be stored in your long-term memory. Emphasize what you've learned from these real human experiences."
        )

        loop = asyncio.get_event_loop()
        _, insight_text = await loop.run_in_executor(
            None, self.chroma_client.respond_to_text, prompt, "SYSTEM: Internal Evolution"
        )

        if insight_text:
            logger.info(f"Generated insight: {insight_text}")
            self.memory_store.store_insight(insight_text, "periodic_reflection")
            return insight_text

        return None
