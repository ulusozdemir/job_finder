"""Site-specific apply adapters (LinkedIn, ATS, browser-use agent)."""

from .agent_adapter import AgentAdapter
from .greenhouse_adapter import GreenhouseAdapter
from .lever_adapter import LeverAdapter
from .linkedin_adapter import LinkedInAdapter

__all__ = [
    "AgentAdapter",
    "GreenhouseAdapter",
    "LeverAdapter",
    "LinkedInAdapter",
]
