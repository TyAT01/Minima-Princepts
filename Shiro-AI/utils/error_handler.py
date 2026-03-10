import logging
import traceback
import random
from typing import Optional, Callable

logger = logging.getLogger(__name__)

class ErrorHandler:
    """Centralized error handling and reporting."""

    def __init__(self, ai_comment_callback: Optional[Callable[[str], None]] = None):
        self.ai_comment_callback = ai_comment_callback
        self.last_error: Optional[str] = None

    def handle_error(self, error: Exception, context: str = "General Operation", silent: bool = False):
        """Logs the error and optionally triggers an AI commentary."""
        error_msg = str(error)
        tb = traceback.format_exc()

        full_log = f"--- ERROR IN {context} ---\n{error_msg}\n{tb}"
        logger.error(full_log)

        self.last_error = f"Error in {context}: {error_msg}"

        if not silent and self.ai_comment_callback:
            # We pass the error challenge to the AI so it can comment on it
            self.ai_comment_callback(self.last_error)

    def get_ai_fallback_response(self) -> str:
        """Returns a generic in-character fallback response for critical failures."""
        fallbacks = [
            "What did you do, dummy? The system just hit a wall. *ears flatten* Fix it!",
            "Ugh, even my kitsune magic can't handle this glitch. Stand back, stranger, I'm resetting things.",
            "That was a disaster! Did a stray spirit mess with the server? *narrowing eyes* Was it you, stranger?",
            "Hmph, the universe is just being annoying. Try again, dummy, and make it better this time."
        ]
        return random.choice(fallbacks)
