"""
task_state.py — Topic & Task State Manager for Shiro AI.

Two layers:
  1. TopicTracker  — tracks current conversation topic(s). Detects when
                     the topic drifts and injects a Shiro-style "poke"
                     into the prompt so she can ask the user to confirm
                     the shift before clearing old context.

  2. StructuredTask — hard authoritative state for tasks that need it,
                      e.g. a tic-tac-toe board. Extends the topic entry.

Usage:
    from task_state import TaskStateManager
    tsm = TaskStateManager()

    tsm.observe(user_text, user_name="Tyler")   # every user message
    block = tsm.prompt_block()                  # inject into system prompt
    tsm.on_shiro_response(shiro_text)           # after Shiro speaks

Fixes in this version:
  - shiro_goes_first flag: tracks who goes first per game, fixes turn-order
  - "you start" / "ladies first" / "you go first" detection sets shiro_goes_first
  - Whose-turn logic uses shiro_goes_first instead of hardcoded user-first
  - [MOVE: position] mandatory token added to board prompt — Shiro must output it
  - on_shiro_response parses [MOVE: position] first, falls back to text extraction
  - Ambiguous single-word position matches ("top","bottom","left","right") removed
    from Shiro-side parsing to prevent false move registration
  - board_text() completely rewritten: clear grid with named row/column labels
  - _TTT_RESET now also fires when game is in progress (abort mid-game)
  - Board-forgotten detection: detects when Tyler corrects the board state and
    rebuilds it from his description
  - Stale TTT guard: if TTT topic goes stale (>10 min idle) Shiro asks to confirm
"""
from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("shiro.task_state")

# ---------------------------------------------------------------------------
#  Topic vocabulary
# ---------------------------------------------------------------------------
_TOPIC_BUCKETS: dict[str, list[str]] = {
    "chatting":    ["hello", "hi", "hey", "name", "who are you", "nice to meet",
                    "my name", "call me", "i am", "i'm", "introduce", "what's up",
                    "whats up", "how are you", "help me", "help", "what do you",
                    "tell me", "talking", "chat", "conversation"],
    "game":        ["game", "play", "round", "rematch", "tic", "tac", "toe",
                    "chess", "checkers", "riddle", "puzzle", "score", "win",
                    "lose", "board", "your move", "my move"],
    "coding":      ["code", "coding", "program", "script", "python", "javascript",
                    "bug", "error", "function", "class", "api", "debug", "compile",
                    "repo", "github", "pull request", "commit"],
    "music":       ["music", "song", "playlist", "album", "artist", "band",
                    "listen", "lyrics", "genre", "concert", "track", "beat"],
    "anime":       ["anime", "manga", "episode", "season", "character", "waifu",
                    "shonen", "isekai", "studio", "arc", "filler"],
    "food":        ["food", "eat", "hungry", "snack", "cook", "recipe", "meal",
                    "dinner", "lunch", "breakfast", "drink", "restaurant", "taste"],
    "streaming":   ["stream", "streaming", "twitch", "youtube", "live", "vtuber",
                    "overlay", "obs", "chat", "viewer", "sub", "donate"],
    "feelings":    ["feel", "feeling", "mood", "sad", "happy", "anxious", "tired",
                    "excited", "bored", "lonely", "stressed", "okay", "fine"],
    "plans":       ["plan", "planning", "going to", "tomorrow", "later", "weekend",
                    "schedule", "event", "trip", "visit", "meeting"],
    "shiro":       ["shiro", "kitsune", "fox", "tail", "ears",
                    "personality", "ai", "model", "memory", "remember"],
    "work_school": ["work", "job", "school", "class", "homework", "project",
                    "deadline", "teacher", "study", "exam", "grade", "assignment"],
}

_MOVE_ON = re.compile(
    r"\b(yes|yeah|yep|yup|sure|ok|okay|move on|new topic|different topic|"
    r"let[''s]?\s+(move|switch|talk\s+about|change)|forget\s+(it|that)|"
    r"never\s+mind|nvm|drop\s+it|done\s+with\s+that|next|next\s+topic)\b",
    re.IGNORECASE,
)
_STAY_ON = re.compile(
    r"\b(no|nope|nah|not\s+yet|stay|keep\s+going|same\s+topic|still\s+on|"
    r"wait|hold\s+on|not\s+done|keep\s+talking\s+about)\b",
    re.IGNORECASE,
)
_POKE_LINES = [
    "Hmm, are we moving on from {topic}, or should I keep that in mind?",
    "Wait — are we done with {topic}? Just want to know before I let it go.",
    "We jumped from {topic} to {new_topic} pretty fast. Intentional?",
    "Should I close the book on {topic}, or are we coming back to it?",
    "So {topic} is officially done, or...?",
    "I'm losing the thread on {topic} — are we dropping it?",
]

# ---------------------------------------------------------------------------
#  Tic-Tac-Toe helpers
# ---------------------------------------------------------------------------

# Full position map — used for USER input (all matches allowed)
_POS_MAP_FULL: dict[str, tuple[int, int]] = {
    "top left":      (0, 0), "top-left":      (0, 0), "topleft":       (0, 0),
    "top right":     (0, 2), "top-right":     (0, 2), "topright":      (0, 2),
    "bottom left":   (2, 0), "bottom-left":   (2, 0), "bottomleft":    (2, 0),
    "bottom right":  (2, 2), "bottom-right":  (2, 2), "bottomright":   (2, 2),
    "top middle":    (0, 1), "top center":    (0, 1), "top-middle":    (0, 1),
    "bottom middle": (2, 1), "bottom center": (2, 1), "bottom-middle": (2, 1),
    "left middle":   (1, 0), "middle left":   (1, 0), "middle-left":   (1, 0),
    "right middle":  (1, 2), "middle right":  (1, 2), "middle-right":  (1, 2),
    "middle":        (1, 1), "center":        (1, 1), "centre":        (1, 1),
    "middle middle": (1, 1), "middle center": (1, 1),
    # Single-word ambiguous matches — ONLY for user input
    "top":           (0, 1),
    "bottom":        (2, 1),
    "left":          (1, 0),
    "right":         (1, 2),
}

# Shiro-side position map — no ambiguous single-word matches.
# Shiro MUST use [MOVE: position]; this is fallback only.
_POS_MAP_SHIRO: dict[str, tuple[int, int]] = {
    k: v for k, v in _POS_MAP_FULL.items()
    if k not in {"top", "bottom", "left", "right"}
}

# Detect "you start" / "ladies first" / Shiro-goes-first phrasing
_SHIRO_FIRST = re.compile(
    r"\b(you\s+start|you\s+go\s+first|ladies\s+first|you\s+first|"
    r"shiro\s+starts?|kitsune\s+first|your\s+turn\s+first|"
    r"you\s+go\s+ahead|go\s+ahead\s+shiro)\b",
    re.IGNORECASE,
)

# [MOVE: position] token — Shiro outputs this so we can parse it reliably
_MOVE_TOKEN = re.compile(r"\[MOVE:\s*([^\]]+)\]", re.IGNORECASE)

_TIC_TAC_START = re.compile(
    r"\b(tic.?tac.?toe|ttt|noughts.and.crosses|let[''s]?\s+play\s+tic|"
    r"play\s+tic|game\s+of\s+tic)\b",
    re.IGNORECASE,
)
_TTT_RESET = re.compile(
    r"\b(new\s+game|rematch|reset|start\s+over|play\s+again|another\s+round|"
    r"best\s+of|next\s+game|one\s+more)\b",
    re.IGNORECASE,
)

# "You forgot the board" / correction detection
_BOARD_CORRECTION = re.compile(
    r"\b(you\s+forgot|forgot\s+the\s+board|wrong\s+board|wrong\s+move|"
    r"that.s\s+(not\s+)?right|you\s+already\s+(went|played|took)|"
    r"remember\s+the\s+board|the\s+board\s+is|here\s+is\s+the\s+board|"
    r"i\s+started\s+at|you\s+started\s+at|the\s+(only\s+)?(open\s+)?slots?\s+(left\s+)?is)\b",
    re.IGNORECASE,
)

# TTT stale timeout — if board sits untouched this long, Shiro asks to confirm
_TTT_STALE_SECS = 600.0  # 10 minutes


def _extract_position(text: str, shiro_side: bool = False) -> Optional[tuple[int, int]]:
    """
    Extract board position from text.

    shiro_side=True: uses the restricted map (no ambiguous single-word matches).
                     Also checks for [MOVE: position] token first.
    shiro_side=False: full map, used for user input.
    """
    # Always try [MOVE: position] token first regardless of side
    m = _MOVE_TOKEN.search(text)
    if m:
        pos_str = m.group(1).strip().lower()
        # Try to resolve the position string
        result = _resolve_pos_str(pos_str, _POS_MAP_FULL)
        if result is not None:
            return result

    pos_map = _POS_MAP_SHIRO if shiro_side else _POS_MAP_FULL
    return _resolve_pos_str(text.lower(), pos_map)


def _resolve_pos_str(text: str, pos_map: dict) -> Optional[tuple[int, int]]:
    """Scan text for position phrase, longest match first."""
    for phrase, pos in sorted(pos_map.items(), key=lambda x: -len(x[0])):
        if phrase in text:
            return pos
    return None


def _check_winner(board: list) -> Optional[str]:
    lines = [
        [(0,0),(0,1),(0,2)], [(1,0),(1,1),(1,2)], [(2,0),(2,1),(2,2)],
        [(0,0),(1,0),(2,0)], [(0,1),(1,1),(2,1)], [(0,2),(1,2),(2,2)],
        [(0,0),(1,1),(2,2)], [(0,2),(1,1),(2,0)],
    ]
    for line in lines:
        vals = [board[r][c] for r, c in line]
        if vals[0] and vals[0] == vals[1] == vals[2]:
            return vals[0]
    if all(board[r][c] for r in range(3) for c in range(3)):
        return "draw"
    return None


@dataclass
class TicTacToeState:
    board: list         = field(default_factory=lambda: [["","",""],["","",""],["","",""]])
    shiro_symbol: str   = "S"
    user_symbol:  str   = "U"
    winner: Optional[str] = None
    move_count: int     = 0
    shiro_goes_first: bool = False   # True when user says "you start" / "ladies first"
    last_move_ts: float = field(default_factory=time.time)

    def apply_move(self, row: int, col: int, symbol: str) -> bool:
        if self.winner or self.board[row][col]:
            return False
        self.board[row][col] = symbol
        self.move_count += 1
        self.last_move_ts = time.time()
        self.winner = _check_winner(self.board)
        return True

    def is_shiro_turn(self) -> bool:
        """True when it is Shiro's turn to move."""
        if self.shiro_goes_first:
            return self.move_count % 2 == 0   # Shiro on even counts (0,2,4,…)
        else:
            return self.move_count % 2 == 1   # Shiro on odd counts (1,3,5,…)

    def open_cells(self):
        return [(r, c) for r in range(3) for c in range(3) if not self.board[r][c]]

    def board_text(self) -> str:
        """
        Render the board as a clear named grid.

        Example output:
              LEFT   | MIDDLE  |  RIGHT
        TOP    [S]   |  [.]    |  [U]
               ---   +  ---   +  ---
        MID    [.]   |  [U]   |  [.]
               ---   +  ---   +  ---
        BOT    [.]   |  [.]   |  [S]

        Cell names:  top-left, top-middle, top-right
                     middle-left, center, middle-right
                     bottom-left, bottom-middle, bottom-right
        """
        row_names = ["TOP   ", "MIDDLE", "BOTTOM"]
        lines = ["         LEFT    | MIDDLE  |  RIGHT "]
        lines.append("         ------  + ------  + ------")
        for r in range(3):
            cells = []
            for c in range(3):
                v = self.board[r][c]
                cells.append(f"  [{v if v else '.'}]   ")
            lines.append(f" {row_names[r]} {'|'.join(cells)}")
            if r < 2:
                lines.append("         ------  + ------  + ------")
        return "\n".join(lines)

    def open_cells_text(self) -> str:
        names = {
            (0,0): "top-left",    (0,1): "top-middle",    (0,2): "top-right",
            (1,0): "middle-left", (1,1): "center",        (1,2): "middle-right",
            (2,0): "bottom-left", (2,1): "bottom-middle", (2,2): "bottom-right",
        }
        return ", ".join(names[p] for p in self.open_cells())

    def is_stale(self) -> bool:
        return (time.time() - self.last_move_ts) > _TTT_STALE_SECS


# ---------------------------------------------------------------------------
#  Topic entry
# ---------------------------------------------------------------------------
@dataclass
class TopicEntry:
    label: str
    bucket: Optional[str]
    started_at: float       = field(default_factory=time.time)
    last_seen_at: float     = field(default_factory=time.time)
    turn_count: int         = 1
    structured_task: object = None   # TicTacToeState or None


# ---------------------------------------------------------------------------
#  TaskStateManager
# ---------------------------------------------------------------------------
class TaskStateManager:
    """
    General topic + structured task state manager.

    Topics are tracked semantically. When a topic shift is detected after
    a substantive conversation (>=3 turns), a poke block is injected into
    the prompt so Shiro naturally asks the user to confirm the shift.

    Structured tasks (like tic-tac-toe) extend a topic with authoritative
    hard state that is always included in the prompt while active.
    """

    BUCKET_HIT_MIN = 1
    POKE_DEPTH_MIN = 3
    DRIFT_TIMEOUT  = 3

    def __init__(self):
        self.topics: list[TopicEntry]       = []
        self._pending_drift: Optional[dict] = None
        self._drift_turns: int              = 0
        self._poke_idx: int                 = 0
        self._board_forgotten: bool         = False  # set when Tyler corrects the board

    # ── Public API -----------------------------------------------------------

    def observe(self, user_text: str, user_name: str = "User") -> None:
        """Process a user message. Call before building the LLM prompt."""
        tl = user_text.lower()

        # 1. Resolve any pending drift confirmation first
        if self._pending_drift:
            self._drift_turns += 1
            if _MOVE_ON.search(tl):
                logger.info(f"[TopicState] Moving on from '{self._pending_drift['old_label']}'")
                self._drop_topic(self._pending_drift["old_label"])
                self._pending_drift = None
                self._drift_turns = 0
            elif _STAY_ON.search(tl):
                logger.info(f"[TopicState] Staying on '{self._pending_drift['old_label']}'")
                old = self._pending_drift["old_label"]
                self._pending_drift = None
                self._drift_turns = 0
                self._touch(old)
                return
            elif self._drift_turns >= self.DRIFT_TIMEOUT:
                logger.info(f"[TopicState] Drift timeout — dropping '{self._pending_drift['old_label']}'")
                self._drop_topic(self._pending_drift["old_label"])
                self._pending_drift = None
                self._drift_turns = 0

        # 2. Detect structured task start (overrides topic classification)
        if _TIC_TAC_START.search(tl):
            # Detect if Shiro goes first at start time
            shiro_first = bool(_SHIRO_FIRST.search(tl))
            self._start_ttt(shiro_first=shiro_first)
            return

        # 3. Handle active tic-tac-toe
        ttt = self._ttt()
        if ttt:
            # TTT reset — allow mid-game abort too
            if _TTT_RESET.search(tl):
                if ttt.winner or ttt.move_count > 0:
                    self._restart_ttt()
                    logger.info("[TopicState] TTT reset by user.")
                return

            # Detect "you start" / "ladies first" after game has begun
            if _SHIRO_FIRST.search(tl) and ttt.move_count == 0:
                ttt.shiro_goes_first = True
                logger.info("[TopicState] TTT: Shiro goes first (detected post-start).")
                self._touch("tic-tac-toe")
                return

            # Board forgotten / correction detection
            if _BOARD_CORRECTION.search(tl) and not ttt.winner:
                self._board_forgotten = True
                logger.info("[TopicState] TTT: Board correction detected — injecting recovery prompt.")
                self._touch("tic-tac-toe")
                return

            # Register user move (game in progress)
            if not ttt.winner:
                pos = _extract_position(tl, shiro_side=False)
                if pos:
                    if ttt.board[pos[0]][pos[1]]:
                        # Cell already occupied — don't apply, let Shiro notice
                        logger.info(f"[TopicState] TTT user tried occupied cell {pos}")
                    else:
                        ok = ttt.apply_move(pos[0], pos[1], ttt.user_symbol)
                        if ok:
                            logger.info(f"[TopicState] TTT user->{pos} winner={ttt.winner}")
                self._touch("tic-tac-toe")
                return

        # 4. Classify message topic
        bucket, hits = self._classify(tl)
        if bucket is None or hits < self.BUCKET_HIT_MIN:
            if self.topics:
                self.topics[-1].last_seen_at = time.time()
                self.topics[-1].turn_count += 1
            return

        current = self.topics[-1] if self.topics else None

        # 5. Same bucket — refresh
        if current and current.bucket == bucket:
            current.last_seen_at = time.time()
            current.turn_count += 1
            return

        # 6. Different bucket — poke before switching
        if (current
                and current.structured_task is None
                and not self._pending_drift
                and current.turn_count >= self.POKE_DEPTH_MIN):
            self._pending_drift = {
                "old_label":  current.label,
                "old_bucket": current.bucket,
                "new_label":  bucket,
            }
            self._drift_turns = 0
            logger.info(f"[TopicState] Drift pending: '{current.label}' -> '{bucket}'")
            return

        # 7. Add new topic
        self._add_topic(bucket, bucket)

    def on_shiro_response(self, text: str) -> None:
        """
        Call after Shiro's response is assembled.
        Parses [MOVE: position] token first; falls back to restricted text extraction.
        Strips the [MOVE: ...] token from Shiro's text before it's displayed.
        """
        ttt = self._ttt()
        if not ttt or ttt.winner:
            return

        # Clear board-forgotten flag — Shiro has now responded
        self._board_forgotten = False

        pos = _extract_position(text, shiro_side=True)
        if pos:
            if ttt.board[pos[0]][pos[1]]:
                logger.info(f"[TopicState] TTT Shiro tried occupied cell {pos} — ignored.")
            else:
                ok = ttt.apply_move(pos[0], pos[1], ttt.shiro_symbol)
                if ok:
                    logger.info(f"[TopicState] TTT shiro->{pos} winner={ttt.winner}")

    def strip_move_token(self, text: str) -> str:
        """Strip [MOVE: ...] tokens from Shiro's response before display/TTS."""
        return _MOVE_TOKEN.sub("", text).strip()

    def prompt_block(self) -> str:
        """Return text to inject into Shiro's system prompt. Empty if nothing active."""
        parts = []

        # Active topic context
        active = [t for t in self.topics if not self._stale(t)]
        if active:
            names = ", ".join(f"'{t.label}'" for t in active)
            cur = active[-1]
            parts.append(
                # FIX: removed [BRACKET HEADERS] — they teach the LLM bracket tokens are valid output.
                f"\nConversation topic(s): {names}. "
                f"You've been on '{cur.label}' for {cur.turn_count} turn(s) — "
                f"stay with it unless the user clearly moves on."
            )

        # Pending drift
        if self._pending_drift:
            _old_topic = self._pending_drift["old_label"]
            _new_topic = self._pending_drift["new_label"]
            tpl = _POKE_LINES[self._poke_idx % len(_POKE_LINES)]
            poke = tpl.format(topic=_old_topic, new_topic=_new_topic)
            self._poke_idx += 1
            parts.append(
                # FIX: plain prose — no bracket tokens that leak into output.
                f"\nTopic may be shifting from \'{_old_topic}\' to \'{_new_topic}\'. "
                f"Naturally check in — something like: \'{poke}\' "
                f"(don't repeat verbatim — say it your way). "
                f"Once confirmed, you can drop \'{_old_topic}\'."
            )

        # Structured task (TTT)
        ttt = self._ttt()
        if ttt:
            parts.append(self._ttt_block(ttt))

        return "\n".join(parts)

    def clear_all(self) -> None:
        self.topics.clear()
        self._pending_drift = None
        self._drift_turns = 0
        self._board_forgotten = False

    def current_topic(self) -> Optional[str]:
        return self.topics[-1].label if self.topics else None

    def is_active(self) -> bool:
        return bool(self.topics)

    # ── Internal helpers -----------------------------------------------------

    def _classify(self, text_lower: str) -> tuple[Optional[str], int]:
        scores: dict[str, int] = {}
        for bucket, kws in _TOPIC_BUCKETS.items():
            hits = sum(1 for kw in kws if kw in text_lower)
            if hits:
                scores[bucket] = hits
        if not scores:
            return None, 0
        best = max(scores, key=lambda b: scores[b])
        return best, scores[best]

    def _add_topic(self, label: str, bucket: Optional[str]) -> None:
        if self.topics and self.topics[-1].label == label:
            return
        self.topics.append(TopicEntry(label=label, bucket=bucket))
        if len(self.topics) > 6:
            self.topics.pop(0)
        logger.info(f"[TopicState] Topic added: '{label}'")

    def _touch(self, label: str) -> None:
        for t in reversed(self.topics):
            if t.label == label:
                t.last_seen_at = time.time()
                t.turn_count += 1
                return

    def _drop_topic(self, label: str) -> None:
        self.topics = [t for t in self.topics if t.label != label]

    def _stale(self, t: TopicEntry) -> bool:
        return (time.time() - t.last_seen_at) / 60.0 > 8

    def _start_ttt(self, shiro_first: bool = False) -> None:
        self.topics = [t for t in self.topics
                       if not isinstance(t.structured_task, TicTacToeState)]
        entry = TopicEntry(
            label="tic-tac-toe", bucket="game",
            structured_task=TicTacToeState(shiro_goes_first=shiro_first)
        )
        self.topics.append(entry)
        if len(self.topics) > 6:
            self.topics.pop(0)
        self._board_forgotten = False
        logger.info(f"[TopicState] Tic-tac-toe started. shiro_first={shiro_first}")

    def _restart_ttt(self) -> None:
        for t in reversed(self.topics):
            if isinstance(t.structured_task, TicTacToeState):
                old = t.structured_task
                t.structured_task = TicTacToeState(shiro_goes_first=old.shiro_goes_first)
                t.turn_count = 1
                t.started_at = time.time()
                t.last_seen_at = time.time()
                self._board_forgotten = False
                logger.info("[TopicState] TTT restarted.")
                return

    def _ttt(self) -> Optional[TicTacToeState]:
        for t in reversed(self.topics):
            if isinstance(t.structured_task, TicTacToeState):
                return t.structured_task
        return None

    def _ttt_block(self, ttt: TicTacToeState) -> str:
        # Determine game status
        if ttt.winner == ttt.shiro_symbol:
            status = "GAME OVER — SHIRO WINS!"
        elif ttt.winner == ttt.user_symbol:
            status = "GAME OVER — USER WINS!"
        elif ttt.winner == "draw":
            status = "GAME OVER — DRAW."
        else:
            shiro_turn = ttt.is_shiro_turn()
            whose = "YOUR TURN (Shiro)" if shiro_turn else "USER'S TURN"
            status = f"Move #{ttt.move_count + 1}. {whose}."

        block = (
            # FIX: plain header — no brackets that teach the model to output them.
            "\n=== ACTIVE GAME: TIC-TAC-TOE ===\n"
            f"BOARD (S=Shiro  U=User  .=empty):\n"
            f"{ttt.board_text()}\n\n"
            f"Status: {status}\n"
        )

        if not ttt.winner:
            block += (
                f"Open cells: {ttt.open_cells_text()}\n\n"
                "GAME RULES FOR SHIRO:\n"
                "  1. The BOARD ABOVE is the ONLY truth. Ignore anything else.\n"
                "  2. Only play in OPEN CELLS listed above.\n"
                "  3. When you make your move, you MUST include the token [MOVE: position-name]\n"
                "     in your response. Example: \"I take center [MOVE: center]\"\n"
                "     This is mandatory — do not skip it.\n"
                "  4. Never play in a cell that is already occupied.\n"
                "  5. If you are unsure of the board state, say so honestly.\n"
            )

            # Stale board warning
            if ttt.is_stale():
                block += (
                    "\nWARNING: The game board has been idle for a while. "
                    "You may have lost track. Ask the user to confirm the current state "
                    "before making your next move.\n"
                )

            # Board forgotten / correction recovery
            if self._board_forgotten:
                block += (
                    "\nBOARD CORRECTION DETECTED: The user just told you something about "
                    "the board that contradicts what you thought. "
                    "Do NOT make a move this turn. Instead: acknowledge the correction, "
                    "and ask the user to confirm the full board state so you can rebuild it.\n"
                    "Say something like: \"I think I lost track — can you tell me exactly "
                    "where all the pieces are so I don't make another mistake?\"\n"
                )

        block += "=== END GAME STATE ==="
        return block