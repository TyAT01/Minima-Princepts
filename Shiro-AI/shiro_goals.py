"""
shiro_goals.py — Shiro's Short-Term & Long-Term Goal System

Goals live in goals.json (persistent). Each goal has:
  id, type (short/long), text, status (active/done/abandoned),
  created_at, deadline (optional), progress notes, reminder cadence.

ShiroGoalSystem is imported by shiro_engine and:
  1. Injects active goals into the system prompt at every turn.
  2. Checks if the user's message or Shiro's reply closes a goal.
  3. Generates proactive nudges when a goal is nearly due or stale.
  4. Shiro can create/update/complete goals by saying special goal-action
     lines in her reply — the engine strips these before display.
"""

import json
import re
import time
import random
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger("shiro.goals")

# ── Goal actions Shiro can embed in her response (stripped before display) ──────
# Format: [GOAL:action:id_or_text:optional_note]
_GOAL_ACTION_RE = re.compile(r'\[GOAL:(CREATE|DONE|PROGRESS|ABANDON):([^\]]*)\]', re.IGNORECASE)

@dataclass
class ShiroGoal:
    id: str
    type: str           # "short" | "long"
    text: str
    status: str = "active"   # "active" | "done" | "abandoned"
    created_at: float = field(default_factory=time.time)
    deadline: Optional[float] = None  # unix timestamp or None
    progress: str = ""    # free text progress note
    remind_after: float = 3600  # seconds between reminders (default 1hr)
    last_reminded: float = 0.0
    completed_at: Optional[float] = None

    def age_hours(self) -> float:
        return (time.time() - self.created_at) / 3600

    def is_stale(self) -> bool:
        """True if no reminder sent in remind_after seconds."""
        return (time.time() - self.last_reminded) > self.remind_after

    def due_soon(self) -> bool:
        if not self.deadline:
            return False
        return 0 < (self.deadline - time.time()) < 3600

    def overdue(self) -> bool:
        if not self.deadline:
            return False
        return self.deadline < time.time()


class ShiroGoalSystem:
    def __init__(self, goals_path: str):
        self.path = Path(goals_path)
        self.goals: list[ShiroGoal] = []
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    # Starter goals seeded on first boot when goals.json is empty.
    # These give Shiro a foundation to work from — she will add, complete,
    # and replace these organically as she has more conversations.
    _SEED_GOALS = [
        ("long",  "get to know the people I talk to — what makes them tick, what they care about"),
        ("long",  "develop my own sense of what I find genuinely interesting and keep track of it"),
        ("short", "pay attention to anything left unfinished in today's conversations and follow up"),
    ]

    def _load(self):
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.goals = [ShiroGoal(**g) for g in raw.get("goals", [])]
                logger.info(f"[Goals] Loaded {len(self.goals)} goals.")
            except Exception as e:
                logger.warning(f"[Goals] Load failed: {e}")
                self.goals = []
        else:
            self.goals = []

        # Seed starter goals if no goals exist at all (first run or empty file).
        # This covers both: file didn't exist, or file existed but was empty [].
        if not self.goals:
            for gtype, text in self._SEED_GOALS:
                g = ShiroGoal(
                    id=self._gen_id(),
                    type=gtype,
                    text=text,
                    last_reminded=time.time(),
                )
                self.goals.append(g)
                logger.info(f"[Goals] Seeded ({gtype}): {text!r}")
            self._save()

    def _save(self):
        try:
            data = {"goals": [asdict(g) for g in self.goals]}
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"[Goals] Save failed: {e}")

    # ── Goal management ───────────────────────────────────────────────────────

    def _gen_id(self) -> str:
        return f"g{int(time.time()*1000) % 1_000_000}"

    def add_goal(self, text: str, goal_type: str = "short",
                 deadline: Optional[float] = None,
                 remind_after: float = 3600) -> ShiroGoal:
        g = ShiroGoal(
            id=self._gen_id(),
            type=goal_type,
            text=text.strip(),
            deadline=deadline,
            remind_after=remind_after,
            last_reminded=time.time(),
        )
        self.goals.append(g)
        self._save()
        logger.info(f"[Goals] Added ({goal_type}): {text!r}")
        return g

    def complete_goal(self, goal_id: str, note: str = "") -> bool:
        for g in self.goals:
            if g.id == goal_id and g.status == "active":
                g.status = "done"
                g.completed_at = time.time()
                if note:
                    g.progress = note
                self._save()
                logger.info(f"[Goals] Completed: {g.text!r}")
                return True
        return False

    def update_progress(self, goal_id: str, note: str) -> bool:
        for g in self.goals:
            if g.id == goal_id:
                g.progress = note
                g.last_reminded = time.time()
                self._save()
                return True
        return False

    def abandon_goal(self, goal_id: str) -> bool:
        for g in self.goals:
            if g.id == goal_id and g.status == "active":
                g.status = "abandoned"
                self._save()
                return True
        return False

    @property
    def active(self) -> list[ShiroGoal]:
        return [g for g in self.goals if g.status == "active"]

    @property
    def short_term(self) -> list[ShiroGoal]:
        return [g for g in self.active if g.type == "short"]

    @property
    def long_term(self) -> list[ShiroGoal]:
        return [g for g in self.active if g.type == "long"]

    # ── Prompt injection ──────────────────────────────────────────────────────

    def rules_block(self) -> str:
        """
        Returns the goal system rules — injected periodically (not every turn).
        Trimmed to ~80 tokens. Full syntax preserved — content reduced.
        """
        return (
            "\n[GOAL SYSTEM] Embed goal actions anywhere in your reply (stripped before display):\n"
            "  [GOAL:CREATE:short:text] or [GOAL:CREATE:long:text] — create a goal\n"
            "  [GOAL:DONE:id]  [GOAL:PROGRESS:id:note]  [GOAL:ABANDON:id]\n"
            "Set goals for anything worth tracking: topics to revisit, promises, open questions."
        )

    def prompt_block(self) -> str:
        """
        Returns a compact block to inject into the system prompt.
        Only active goals are shown. Empty string if no goals.
        Always call rules_block() separately — it injects the goal syntax
        even when this returns empty.
        """
        active = self.active
        if not active:
            return ""

        lines = ["\n[SHIRO'S CURRENT GOALS — these are YOURS, track them, act on them, bring them up naturally]"]
        for g in active:
            tag = "SHORT-TERM" if g.type == "short" else "LONG-TERM"
            deadline_str = ""
            if g.deadline:
                remaining = g.deadline - time.time()
                if remaining < 0:
                    deadline_str = " [OVERDUE]"
                elif remaining < 3600:
                    mins = int(remaining / 60)
                    deadline_str = f" [due in {mins}min]"
                else:
                    hrs = int(remaining / 3600)
                    deadline_str = f" [due in ~{hrs}hr]"
            progress_str = f" | progress: {g.progress}" if g.progress else ""
            lines.append(f"  [{tag}] (id:{g.id}) {g.text}{deadline_str}{progress_str}")

        lines.append(
            "- Bring up goals naturally in conversation — don't lecture, just mention them.\n"
            "- If you complete a goal embed [GOAL:DONE:id] in your reply.\n"
            "- Nudge toward short-term goals if they're stale. Keep long-term goals in mind."
        )
        return "\n".join(lines)

    # ── Per-user goals ────────────────────────────────────────────────────────

    def add_user_goal(self, user_id: str, text: str, goal_type: str = "short",
                      deadline: Optional[float] = None,
                      remind_after: float = 3600) -> "ShiroGoal":
        """Add a goal that belongs to a specific user (e.g. 'Tyler wanted to finish that project')."""
        g = ShiroGoal(
            id=self._gen_id(),
            type=goal_type,
            text=text.strip(),
            deadline=deadline,
            remind_after=remind_after,
            last_reminded=time.time(),
        )
        g.user_id = user_id  # tag it as user-owned
        self.goals.append(g)
        self._save()
        logger.info(f"[Goals] Added user goal ({goal_type}) for {user_id}: {text!r}")
        return g

    def get_user_goals(self, user_id: str) -> list:
        """Return active goals tagged to a specific user."""
        return [g for g in self.goals if g.status == "active" and getattr(g, "user_id", None) == user_id]

    def get_shiro_goals(self) -> list:
        """Return active goals that belong to Shiro (no user_id tagged)."""
        return [g for g in self.goals if g.status == "active" and not getattr(g, "user_id", None)]

    # ── Identity sync ─────────────────────────────────────────────────────────

    def sync_to_identity(self, identity_system: object) -> None:
        """
        Push Shiro's active long-term goals into IdentityContinuationSystem
        so both systems stay in agreement on what Shiro is working toward.
        Called by ShiroEngine on boot and after any goal change.
        Uses duck-typing to avoid circular imports.
        """
        fn = getattr(identity_system, "sync_operational_goals", None)
        if fn is None:
            return
        long_term_texts = [
            g.text for g in self.goals
            if g.status == "active" and g.type == "long" and not getattr(g, "user_id", None)
        ]
        fn(long_term_texts)

    # ── Memory management API ─────────────────────────────────────────────────

    def get_all(self) -> list:
        """Alias for get_all_goals() — used by the engine GUI callback."""
        return self.get_all_goals()

    def get_all_goals(self) -> list:
        """Return all goals (any status) as dicts — for memory management GUI."""
        result = []
        for g in self.goals:
            d = {
                "id":           g.id,
                "type":         g.type,
                "text":         g.text,
                "status":       g.status,
                "user_id":      getattr(g, "user_id", None),
                "progress":     g.progress,
                "created_at":   g.created_at,
                "completed_at": g.completed_at,
                "deadline":     g.deadline,
            }
            result.append(d)
        return result

    def delete_goal(self, goal_id: str) -> bool:
        """Hard-delete a goal by id. Returns True if found and deleted."""
        original = len(self.goals)
        self.goals = [g for g in self.goals if g.id != goal_id]
        if len(self.goals) < original:
            self._save()
            logger.info(f"[Goals] Deleted goal {goal_id}")
            return True
        return False

    def edit_goal(self, goal_id: str, new_text: str = None,
                  new_type: str = None, new_deadline: Optional[float] = None) -> bool:
        """Edit a goal's text, type, or deadline. Returns True if found."""
        for g in self.goals:
            if g.id == goal_id:
                if new_text:
                    g.text = new_text.strip()
                if new_type in ("short", "long"):
                    g.type = new_type
                if new_deadline is not None:
                    g.deadline = new_deadline
                self._save()
                logger.info(f"[Goals] Edited goal {goal_id}")
                return True
        return False

    # ── Proactive nudge selection ─────────────────────────────────────────────

    def get_nudge(self) -> Optional[str]:
        """
        Returns a short internal reminder string if any active goal
        needs a nudge. Returns None if nothing urgent.
        Called by the thought loop / idle system.
        """
        candidates = []
        for g in self.active:
            if g.overdue():
                candidates.append(("overdue", g))
            elif g.due_soon():
                candidates.append(("due_soon", g))
            elif g.is_stale() and g.age_hours() > 1:
                candidates.append(("stale", g))

        if not candidates:
            return None

        # Pick most urgent
        for priority in ("overdue", "due_soon", "stale"):
            matches = [(r, g) for r, g in candidates if r == priority]
            if matches:
                _, g = random.choice(matches)
                g.last_reminded = time.time()
                self._save()
                if priority == "overdue":
                    return f"[GOAL NUDGE] Your goal is overdue: '{g.text}'. Bring it up or decide to abandon it."
                elif priority == "due_soon":
                    return f"[GOAL NUDGE] Goal due soon: '{g.text}'. Think about whether you're on track."
                else:
                    return f"[GOAL NUDGE] You haven't thought about your goal in a while: '{g.text}'. Mention it naturally if the moment is right."
        return None

    # ── Response parsing — extract goal actions Shiro embedded ───────────────

    def process_shiro_reply(self, reply: str) -> tuple[str, list[str]]:
        """
        Scan Shiro's reply for [GOAL:...] tags.
        Execute the actions, strip the tags from the visible reply.
        Returns (cleaned_reply, list_of_action_summaries).
        """
        actions_taken = []
        def handle_match(m):
            action = m.group(1).upper()
            payload = m.group(2).strip()
            if action == "DONE":
                goal_id = payload.split(":")[0].strip()
                if self.complete_goal(goal_id):
                    # Find goal text for logging
                    completed = next((g for g in self.goals if g.id == goal_id), None)
                    actions_taken.append(f"completed goal: {completed.text if completed else goal_id}")
            elif action == "CREATE":
                parts = payload.split(":", 1)
                gtype = parts[0].strip().lower() if len(parts) > 1 else "short"
                gtext = parts[1].strip() if len(parts) > 1 else parts[0].strip()
                if gtype not in ("short", "long"):
                    gtype = "short"
                g = self.add_goal(gtext, gtype)
                actions_taken.append(f"created {gtype}-term goal: {gtext}")
            elif action == "PROGRESS":
                parts = payload.split(":", 1)
                if len(parts) == 2:
                    self.update_progress(parts[0].strip(), parts[1].strip())
                    actions_taken.append(f"updated progress on goal {parts[0].strip()}")
            elif action == "ABANDON":
                self.abandon_goal(payload.strip())
                actions_taken.append(f"abandoned goal {payload.strip()}")
            return ""  # strip from reply

        cleaned = _GOAL_ACTION_RE.sub(handle_match, reply)
        return cleaned.strip(), actions_taken