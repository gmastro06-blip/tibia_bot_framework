"""Bestiary utilities.

This package provides fuzzy matching between noisy labels (OCR/detector) and a
canonical creature registry.
"""

from .matcher import BestiaryMatcher, BestiaryMatchConfig, MatchResult

__all__ = ["BestiaryMatcher", "BestiaryMatchConfig", "MatchResult"]
