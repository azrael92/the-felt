"""Rule-based opponent agents organized by archetype."""

from the_felt.agents.archetype import REGISTRY, Archetype
from the_felt.agents.policy import decide
from the_felt.agents.tag import decide_tag

__all__ = ["Archetype", "REGISTRY", "decide", "decide_tag"]
