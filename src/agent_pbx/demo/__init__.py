"""Isolated fixtures and browser delivery for the public Agent PBX demo."""

from .contract import DEMO_CONTRACT_VERSION, DEMO_SCENARIO_ID, public_demo_contract

__all__ = [
    "DEMO_CONTRACT_VERSION",
    "DEMO_SCENARIO_ID",
    "public_demo_contract",
]
