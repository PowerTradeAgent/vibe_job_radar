"""Evidence-first requirement extraction; no network calls on import."""
from ._version import __version__
from .models import JobRecord, Requirement
from .extract import RuleExtractor
from .pipeline import analyze
__all__ = ["JobRecord", "Requirement", "RuleExtractor", "analyze"]
