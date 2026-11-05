"""Slide decks built like reports: ``sty.Deck.open("decks/pbmc", title=...)``. See deck.py."""

try:
    import lxml  # noqa: F401
    import pptx  # noqa: F401
except ImportError as error:  # pragma: no cover - depends on the environment
    raise ImportError('storyteller decks need python-pptx: pip install "storyteller[deck]"') from error

from .deck import Deck, is_deck, paths

__all__ = ["Deck", "is_deck", "paths"]
