"""iTerm2 sRGB color space fix for pixel art icon display.

Some iTerm2 color presets (notably "Dark Background") use a legacy
"Calibrated" color space where foreground and background colors render
differently for the same RGB value. This causes visible banding in
half-block pixel art. Switching to explicit sRGB fixes it.

Uses iTerm2's Python API to apply session-local color overrides —
the underlying profile and other sessions are unaffected.

Requires: pip install iterm2, and iTerm2 Python API enabled
(Preferences > General > Magic > "Enable Python API").
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_hint_shown = False

# All color properties to convert: (getter_name, setter_name)
_COLOR_PROPS = [
    ("foreground_color", "set_foreground_color"),
    ("background_color", "set_background_color"),
    ("bold_color", "set_bold_color"),
    ("cursor_color", "set_cursor_color"),
    ("cursor_text_color", "set_cursor_text_color"),
    ("selection_color", "set_selection_color"),
    ("selected_text_color", "set_selected_text_color"),
    ("link_color", "set_link_color"),
] + [
    (f"ansi_{i}_color", f"set_ansi_{i}_color") for i in range(16)
]


def _is_iterm2() -> bool:
    return os.environ.get("TERM_PROGRAM") == "iTerm.app"


def ensure_srgb() -> list | None:
    """Switch current iTerm2 session colors to explicit sRGB.

    Returns the original Color objects for restoration, or None if
    not in iTerm2 / API unavailable / package not installed.
    """
    if not _is_iterm2():
        return None

    try:
        import iterm2
    except ImportError:
        logger.debug("iterm_colors: iterm2 package not installed")
        return None

    originals = []

    async def _apply(connection):
        app = await iterm2.async_get_app(connection)
        session = app.current_terminal_window.current_tab.current_session
        profile = await session.async_get_profile()

        change = iterm2.LocalWriteOnlyProfile()
        for getter, setter in _COLOR_PROPS:
            color = getattr(profile, getter, None)
            if color is None:
                continue
            originals.append((setter, color))
            srgb_color = iterm2.Color(
                color.red, color.green, color.blue, color.alpha,
                color_space=iterm2.ColorSpace.SRGB,
            )
            getattr(change, setter)(srgb_color)

        await session.async_set_profile_properties(change)

    try:
        iterm2.run_until_complete(_apply)
        logger.debug("iterm_colors: applied sRGB overrides (%d colors)", len(originals))
        return originals
    except SystemExit:
        logger.debug("iterm_colors: could not connect to iTerm2 API")
        return None
    except Exception as exc:
        logger.debug("iterm_colors: unexpected error: %s", exc)
        return None


def restore_colors(originals: list) -> None:
    """Restore original iTerm2 session colors after sRGB override."""
    try:
        import iterm2
    except ImportError:
        return

    async def _restore(connection):
        app = await iterm2.async_get_app(connection)
        session = app.current_terminal_window.current_tab.current_session

        change = iterm2.LocalWriteOnlyProfile()
        for setter, color in originals:
            getattr(change, setter)(color)

        await session.async_set_profile_properties(change)

    try:
        iterm2.run_until_complete(_restore)
        logger.debug("iterm_colors: restored original colors")
    except (SystemExit, Exception) as exc:
        logger.debug("iterm_colors: restore failed: %s", exc)


def show_hint_if_needed() -> None:
    """Print a one-time hint about enabling iTerm2 Python API."""
    global _hint_shown
    if _hint_shown or not _is_iterm2():
        return
    _hint_shown = True

    from yoto_cli.progress import _console
    _console.print(
        "[dim]Tip: For improved icon display, enable iTerm2's Python API "
        "(Preferences > General > Magic) and install iterm2 "
        "(pip install iterm2)[/dim]"
    )
