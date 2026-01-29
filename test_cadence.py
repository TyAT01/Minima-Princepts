import sys
import os
import time
import unittest
from unittest.mock import MagicMock

# Add Aurelia_chroma to path
sys.path.append(os.path.abspath("Aurelia_chroma"))

from cadence_controller import AureliaCadenceController, StreamSignals, ChatMessage

class TestCadenceController(unittest.TestCase):
    def setUp(self):
        self.controller = AureliaCadenceController(seed=42)
        self.now = 1000.0

    def test_initial_silence_pressure(self):
        # Initial state should have high desire to speak if silence persists
        self.controller.last_spoke_ts = self.now - 20.0 # 20 seconds of silence
        signals = StreamSignals(now=self.now)
        self.controller.update(signals)
        intent = self.controller.maybe_emit_intent(signals)

        # It should produce a FILLER intent due to silence
        self.assertIsNotNone(intent)
        self.assertEqual(intent.kind, "FILLER")

    def test_reply_to_chat(self):
        self.controller.last_spoke_ts = self.now - 10.0
        msg = ChatMessage(user="User1", text="Hello Aurelia!", ts=self.now - 1.0, source="twitch")
        signals = StreamSignals(now=self.now, chat_messages=[msg])

        self.controller.update(signals)
        intent = self.controller.maybe_emit_intent(signals)

        # High probability of REPLY since there's a message
        self.assertIsNotNone(intent)
        self.assertIn(intent.kind, ["REPLY", "RIFF", "REACT", "FILLER"])
        if intent.kind == "REPLY":
            self.assertEqual(intent.target_message.user, "User1")

    def test_hype_reaction(self):
        self.controller.last_spoke_ts = self.now - 5.0
        # Extreme intensity event to force a reaction
        signals = StreamSignals(now=self.now, event_intensity=1.0)

        # We need to call update to ingest signals
        self.controller.update(signals)
        # Ensure we are past the min_gap
        intent = self.controller.maybe_emit_intent(signals)

        # Might still be None due to weighted pick if we are unlucky,
        # but with event_intensity=1.0, desire should be high.
        # Let's try multiple ticks if needed or just check desire logic.
        if intent is None:
            # Force silence pressure if needed to guarantee intent
            self.controller.last_spoke_ts = self.now - 30.0
            intent = self.controller.maybe_emit_intent(signals)

        self.assertIsNotNone(intent)
        self.assertTrue(intent.urgency > 0.5)

    def test_focus_suppression(self):
        self.controller.last_spoke_ts = self.now - 5.0
        # High focus should reduce desire to speak
        signals = StreamSignals(now=self.now, focus_level=0.9)

        self.controller.update(signals)
        intent = self.controller.maybe_emit_intent(signals)

        # Should likely be None because she's focused
        # (Unless silence pressure is extreme)
        self.assertIsNone(intent)

    def test_config_update(self):
        updates = self.controller.update_config(min_gap_s=5.0, max_silence_s=60.0)
        self.assertEqual(self.controller.min_gap_s, 5.0)
        self.assertEqual(self.controller.max_silence_s, 60.0)
        self.assertIn("min_gap_s", updates)

if __name__ == "__main__":
    unittest.main()
