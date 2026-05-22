"""Deterministic risk layer.

Sits between the LLM agent output and order execution. The LLM may suggest a
trade; this layer decides whether to allow it and at what size.

Key invariant: nothing in this layer calls an LLM. All decisions are
deterministic, auditable, and unit-testable.
"""
