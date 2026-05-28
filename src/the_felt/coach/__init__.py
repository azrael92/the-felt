"""Coach: decision context computation + tiered explanations + LLM polish."""

from the_felt.coach.analyzer import DecisionContext, compute_decision_context
from the_felt.coach.explain import render_tier
from the_felt.coach.llm_polish import answer_question, polish

__all__ = [
    "DecisionContext", "compute_decision_context",
    "render_tier", "polish", "answer_question",
]
