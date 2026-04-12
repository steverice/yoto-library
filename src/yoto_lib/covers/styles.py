"""Cover art style definitions for AI-generated covers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class CoverStyle:
    """A visual art style for cover generation.

    Instances self-register on creation. Access via class methods:
    - CoverStyle.get(name) — lookup by name
    - CoverStyle.default() — the default style
    - CoverStyle.names() — frozenset of all registered names
    """

    _registry: ClassVar[dict[str, CoverStyle]] = {}
    _default: ClassVar[CoverStyle | None] = None

    name: str
    label: str
    illustration_prompt: str
    title_prompt: str
    is_default: bool = False

    def __post_init__(self) -> None:
        if self.name in self._registry:
            raise ValueError(f"Duplicate style: {self.name!r}")
        self._registry[self.name] = self
        if self.is_default:
            if type(self)._default is not None:
                raise ValueError("Multiple default styles")
            type(self)._default = self

    @classmethod
    def get(cls, name: str) -> CoverStyle:
        """Return the style with the given name, or raise ValueError."""
        try:
            return cls._registry[name]
        except KeyError:
            raise ValueError(f"Unknown style: {name!r}") from None

    @classmethod
    def default(cls) -> CoverStyle:
        """Return the default style."""
        if cls._default is None:
            raise RuntimeError("No default style registered")
        return cls._default

    @classmethod
    def names(cls) -> frozenset[str]:
        """Return the set of all registered style names."""
        return frozenset(cls._registry)


# ── Style instances ──────────────────────────────────────────────────────────

CARTOON = CoverStyle(
    name="cartoon",
    label="Bold outlines, bright colors, animated TV show look",
    illustration_prompt=(
        "Use a cartoon style with bold black outlines, bright saturated colors, "
        "and simple rounded shapes like an animated TV show."
    ),
    title_prompt=(
        "Use bold, playful cartoon lettering with thick outlines that matches "
        "the animated cartoon style of the illustration."
    ),
)

STORYBOOK = CoverStyle(
    name="storybook",
    label="Soft, painterly, warm lighting and gentle textures",
    illustration_prompt=(
        "Use a classic storybook illustration style with soft, painterly brushwork, "
        "warm lighting, and gentle textures like a traditional children's picture book."
    ),
    title_prompt=(
        "Use elegant, classic storybook lettering with warm tones that matches the painterly style of the illustration."
    ),
    is_default=True,
)

WATERCOLOR = CoverStyle(
    name="watercolor",
    label="Loose brushstrokes, translucent washes, paper texture",
    illustration_prompt=(
        "Use a watercolor style with loose, flowing brushstrokes, translucent color washes, and visible paper texture."
    ),
    title_prompt=(
        "Use soft, hand-painted watercolor lettering with gentle color washes "
        "that matches the watercolor style of the illustration."
    ),
)

PAPERCRAFT = CoverStyle(
    name="papercraft",
    label="Cut-paper or felt collage with layered depth",
    illustration_prompt=(
        "Use a papercraft collage style with layered cut-paper or felt shapes, "
        "visible texture, and a sense of tactile depth."
    ),
    title_prompt=(
        "Use cut-out paper lettering with visible texture and slight shadows "
        "that matches the papercraft collage style of the illustration."
    ),
)

CHALK = CoverStyle(
    name="chalk",
    label="Soft pastel chalk on dark or colored paper",
    illustration_prompt=(
        "Use a chalk pastel style on dark or colored paper with soft, warm edges and slightly rough, powdery texture."
    ),
    title_prompt=(
        "Use soft chalk lettering with slightly rough, powdery edges that matches "
        "the pastel chalk style of the illustration."
    ),
)

CEL = CoverStyle(
    name="cel",
    label="Flat colors, clean vector edges, modern animation",
    illustration_prompt=(
        "Use a cel-shaded style with flat colors, clean vector-style edges, "
        "minimal shading, and a polished modern animation feel."
    ),
    title_prompt=(
        "Use clean, modern sans-serif lettering with flat colors and crisp edges "
        "that matches the cel-shaded style of the illustration."
    ),
)

GOUACHE = CoverStyle(
    name="gouache",
    label="Thick matte paint, visible brushwork, rich colors",
    illustration_prompt=(
        "Use a gouache painting style with thick, matte, opaque paint, visible brushwork, and rich saturated colors."
    ),
    title_prompt=(
        "Use hand-painted lettering with thick, opaque brushstrokes and rich "
        "colors that matches the gouache style of the illustration."
    ),
)

CRAYON = CoverStyle(
    name="crayon",
    label="Waxy textured strokes, childlike and tactile",
    illustration_prompt=(
        "Use a crayon drawing style with waxy, textured strokes, visible color "
        "layering, and a childlike, tactile quality."
    ),
    title_prompt=(
        "Use hand-drawn crayon lettering with waxy texture and visible strokes "
        "that matches the crayon style of the illustration."
    ),
)
