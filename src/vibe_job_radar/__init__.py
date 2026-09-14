"""Evidence-first requirement extraction; no network calls on import."""
__version__ = "0.2.0"
from .models import JobRecord, Requirement
from .extract import RuleExtractor
from .pipeline import analyze
__all__ = ["JobRecord", "Requirement", "RuleExtractor", "analyze"]
