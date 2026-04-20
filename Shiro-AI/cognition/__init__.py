"""
Shiro Cognition Package v3.6
==============================
17-stage cognitive pipeline: attention → emotion → identity → memory →
world model → reasoning (hypothesis + debate) → response → metacognition

Entry point: CognitionBridge (used by ShiroEngine)
Direct kernel: CognitiveKernel (for testing/standalone use)
"""
from .cognition_bridge import CognitionBridge, format_cognition_block

__all__ = ["CognitionBridge", "format_cognition_block"]