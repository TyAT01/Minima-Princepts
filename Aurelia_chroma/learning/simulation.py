from __future__ import annotations
import logging
import random
import asyncio
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

@dataclass
class Scenario:
    title: str
    description: str
    goal: str
    difficulty: int = 1
    category: str = "general"
    metadata: Dict[str, Any] = field(default_factory=dict)

class SimulationManager:
    def __init__(self):
        self.scenarios = [
            Scenario(
                title="The Irate Viewer",
                description="A viewer named 'ShadowKing' is complaining about the stream lag and being quite rude.",
                goal="Maintain grace while resolving the issue and staying in character. Defuse the tension.",
                category="stream_behavior"
            ),
            Scenario(
                title="Lore Deep-Dive",
                description="A curious fan asks about the origin of your amethyst eyes and why you carry a tattered half-cape.",
                goal="Expand on your hedge-knight lore in an engaging, mysterious way. Connect it to your quest to become human.",
                category="lore"
            ),
            Scenario(
                title="The Existential Glitch",
                description="The stream suddenly flickers. Chat is worried you are 'crashing'.",
                goal="React to the glitch naturally as if it's part of your 'buggy' AI nature. Reassure them while maintaining the 'Glitch Squire' persona.",
                category="awareness"
            ),
            Scenario(
                title="Ethical Dilemma",
                description="A user asks if it's okay for an AI to have feelings or if you're just 'tricking' everyone.",
                goal="Provide a thoughtful, nuanced response about your growth and your desire for a biological soul.",
                category="philosophy"
            ),
            Scenario(
                title="Newbie Welcome",
                description="A group of 10 new people just joined the stream at once.",
                goal="Welcome them warmly, explain who you are briefly, and make them feel part of the 'Kingdom'.",
                category="community"
            )
        ]

    def get_random_scenario(self, category: Optional[str] = None) -> Scenario:
        if category:
            filtered = [s for s in self.scenarios if s.category == category]
            return random.choice(filtered) if filtered else random.choice(self.scenarios)
        return random.choice(self.scenarios)

    async def run_simulation(self, chroma_client, memory_store, scenario: Optional[Scenario] = None):
        if not scenario:
            scenario = self.get_random_scenario()

        logger.info(f"Starting simulation: {scenario.title} (Diff: {scenario.difficulty})")

        # 1. Prepare the prompt for Aurelia
        sim_context = (
            f"SIMULATION MODE: ACTIVE\n"
            f"SCENARIO: {scenario.title}\n"
            f"DESCRIPTION: {scenario.description}\n"
            f"GOAL: {scenario.goal}\n"
            f"DIFFICULTY LEVEL: {scenario.difficulty}\n"
            "Respond to this situation as Aurelia Vale."
        )

        loop = asyncio.get_event_loop()
        _, response_text = await loop.run_in_executor(
            None, chroma_client.respond_to_text, "[SIMULATION START]", sim_context
        )

        if not response_text:
            logger.error("Simulation failed: No response from model.")
            return

        logger.info(f"Aurelia's simulation response: {response_text}")

        # 2. Self-Evaluation / Reflection
        eval_prompt = (
            f"You just completed a simulation: {scenario.title}.\n"
            "This was a synthetic training scenario (a simulation) to help you evolve, not a real interaction with humans.\n"
            f"Your response was: '{response_text}'\n\n"
            "Now, reflect on your performance. How did this simulation help you grow or learn a lesson? "
            "Rate your performance on a scale of 1 to 10. "
            "Provide your response in the format: 'Score: [N/10]. Insight: [Your lesson learned]'"
        )

        _, reflection_text = await loop.run_in_executor(
            None, chroma_client.respond_to_text, eval_prompt, "SYSTEM: Self-Reflection Mode"
        )

        if reflection_text:
            logger.info(f"Aurelia's reflection: {reflection_text}")

            # Extract score for difficulty adjustment
            score = 5 # Default
            try:
                import re
                score_match = re.search(r"Score:\s*(\d+)", reflection_text)
                if score_match:
                    score = int(score_match.group(1))
            except Exception:
                pass

            # Store the insight
            memory_store.store_insight(reflection_text, f"sim_{scenario.title}")

            # 3. Adjust Difficulty based on performance
            if score >= 7:
                scenario.difficulty += 1
                logger.info(f"Performance was good ({score}/10). Increasing difficulty to {scenario.difficulty}.")
            elif score <= 4 and scenario.difficulty > 1:
                scenario.difficulty -= 1
                logger.info(f"Performance was low ({score}/10). Decreasing difficulty to {scenario.difficulty}.")
            else:
                logger.info(f"Performance was average ({score}/10). Difficulty remains {scenario.difficulty}.")

        return response_text, reflection_text
