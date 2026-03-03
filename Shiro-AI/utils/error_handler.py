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
        # FIX: Removed *ears flatten* and *narrowing eyes* — asterisk actions violate persona rules.
        # Also softened "dummy" x2 — errors are usually system failures, not user ones.
        # Kept Shiro's voice: deflective, slightly flustered, not actually hostile.
        fallbacks = [
            "Hmph. Something broke and it wasn't graceful. Try again.",
            "Ugh, even my kitsune magic has limits. Stand back — I'm resetting things.",
            "That was... not ideal. Did a stray spirit mess with the server? Try again, stranger.",
            "The universe is being annoying today. Try again and maybe it'll cooperate this time."
        ]
        return random.choice(fallbacks)
