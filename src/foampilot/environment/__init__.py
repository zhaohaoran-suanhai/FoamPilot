"""Local OpenFOAM environment facts."""

from .discovery import discover_environment, enrich_command_help
from .models import CommandFact, EnvironmentSnapshot

__all__ = [
    "CommandFact",
    "EnvironmentSnapshot",
    "discover_environment",
    "enrich_command_help",
]
