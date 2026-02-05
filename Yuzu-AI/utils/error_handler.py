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
            # We pass the error description to the AI so it can comment on it
            self.ai_comment_callback(self.last_error)

    def get_ai_fallback_response(self) -> str:
        """Returns a generic in-character fallback response for critical failures."""
        fallbacks = [
            "Ugh, lag! 🙄 My setup is acting up. Give me a sec, I need to check the router. 🔌",
            "No shot! The stream just crashed! 📉 Probably your internet, but I'll 'fix' it anyway. Hmph! ✨",
            "Skill issue! Wait, no, that was a server glitch. 🛠️ Let me reboot real quick, dummy! 💢",
            "Pfft, my PC is overheating because I'm too cracked at gaming. 🎮 One second while I cool it down! 🧊"
        ]
        return random.choice(fallbacks)
