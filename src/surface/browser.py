"""A browser-backed :class:`~surface.model.Surface`.

Two things here are not incidental.

**Frames are enumerated, not assumed.** Reading the accessibility tree of the
target's top document returns only its ``<noframes>`` fallback -- measured, not
guessed. The console's entire content lives in child frames, so every
observation walks the frame tree and merges what each frame reports, tagging
nodes with the path they were found at.

**Element handles never leave the page.** The capture script parks the
elements it found on ``window.__px``; a ref is just a frame index and a
position in that array. Nothing is written into the application's markup, so
observing the surface cannot change how it behaves -- which matters when the
whole exercise is deciding whether a replay is deterministic.
"""

from __future__ import annotations

import contextlib
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Frame, Page, sync_playwright

from .naming import build_nodes
from .model import ActionFailed, Node, Observation, StaleElement

SCAN_SCRIPT = (Path(__file__).parent / "scan.js").read_text()

# An observation has to fit in a model's context and stay cheap to take. The
# target's densest page is well inside this; the flag on Observation says when
# a page was not fully captured rather than letting it pass silently.
MAX_NODES_PER_FRAME = 400


class BrowserSurface:
    """Drives one browser page as a perceivable, actionable surface."""

    def __init__(self, page: Page, *, max_nodes: int = MAX_NODES_PER_FRAME) -> None:
        self._page = page
        self._max_nodes = max_nodes
        # frame index -> Frame, rebuilt on every observation because navigation
        # invalidates frame objects.
        self._frames: dict[int, Frame] = {}
        self._token = ""

    # -- perceiving --------------------------------------------------------

    @property
    def url(self) -> str:
        return self._page.url

    def observe(self) -> Observation:
        self._settle()
        self._frames = {}
        self._token = secrets.token_hex(3)
        nodes: list[Node] = []
        frame_paths: list[tuple[str, ...]] = []
        truncated = False

        for index, frame in enumerate(self._page.frames):
            path = self._frame_path(frame)
            frame_paths.append(path)
            self._frames[index] = frame

            try:
                captured = frame.evaluate(SCAN_SCRIPT, self._max_nodes)
            except PlaywrightError:
                # A frame can be mid-navigation or detached. Skipping it is
                # correct -- the next observation will pick it up -- but it is
                # reported so a caller is never silently shown a partial page.
                truncated = True
                continue

            truncated = truncated or bool(captured.get("truncated"))
            nodes.extend(
                build_nodes(
                    captured.get("records") or [],
                    frame_path=path,
                    ref_prefix=f"{self._token}:f{index}:",
                )
            )

        return Observation(
            url=self._page.url,
            title=self._page.title(),
            nodes=tuple(nodes),
            frames=tuple(frame_paths),
            captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            truncated=truncated,
            token=self._token,
        )

    def screenshot(self) -> bytes:
        return self._page.screenshot(full_page=False)

    # -- acting ------------------------------------------------------------

    def goto(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded")

    def _act(self, what: str, action) -> None:
        """Run one action, translating driver failures into surface errors."""
        try:
            action()
        except PlaywrightError as exc:
            message = str(exc)
            if "not attached" in message or "detached" in message:
                raise StaleElement(
                    f"the control for {what} left the page between observing "
                    f"it and acting on it"
                ) from None
            raise ActionFailed(f"{what} did not complete: {message.splitlines()[0]}") from None

    def click(self, ref: str) -> None:
        handle = self._handle(ref)
        self._act("this click", handle.click)
        self._await_any_navigation()

    def fill(self, ref: str, text: str) -> None:
        handle = self._handle(ref)

        def typed() -> None:
            handle.fill("")
            handle.type(text)

        self._act("this text field", typed)

    def select(self, ref: str, value: str) -> None:
        """Select by visible label, falling back to the option's value.

        Label first on purpose: an artifact that records "Vacation Club
        Savings" stays readable by a human reviewer and stays correct if the
        vendor renumbers its product codes, whereas one recording "SAV-V" does
        neither.
        """
        handle = self._handle(ref)

        def chosen() -> None:
            # Decide how to match *before* acting. Attempting a label match
            # and letting it fail costs the driver's full timeout -- thirty
            # seconds per selection made by value, silently, on every run.
            # Reading the options is instant and tells the caller what was
            # actually on offer when nothing matches.
            found = handle.evaluate(
                """(el, wanted) => {
                    const options = Array.from(el.options);
                    if (options.some(o => (o.textContent || "").trim() === wanted))
                        return { by: "label" };
                    if (options.some(o => o.value === wanted))
                        return { by: "value" };
                    return {
                        by: null,
                        available: options.map(o => (o.textContent || "").trim()),
                    };
                }""",
                value,
            )
            if found["by"] == "label":
                handle.select_option(label=value)
            elif found["by"] == "value":
                handle.select_option(value=value)
            else:
                raise ActionFailed(
                    f"no option matches {value!r}; this list offers "
                    f"{found['available']}"
                )

        self._act(f"the option {value!r}", chosen)

    def press(self, ref: str, key: str) -> None:
        handle = self._handle(ref)
        self._act(f"the key {key!r}", lambda: handle.press(key))

    def close(self) -> None:
        with contextlib.suppress(PlaywrightError):
            self._page.close()

    # -- internals ---------------------------------------------------------

    def _await_any_navigation(self) -> None:
        """Let a navigation the click started actually begin, then finish.

        A click returns as soon as the event is dispatched. On a
        server-rendered application that means the *old* page is still on
        screen for a moment, and an observation taken immediately would report
        the form that was just submitted rather than the page it produced --
        which is how a caller ends up looking for search results on the search
        form. The short grace period is for the navigation to start; the load
        wait is for it to complete, and both are no-ops when the click did not
        navigate at all.
        """
        self._page.wait_for_timeout(120)
        with contextlib.suppress(PlaywrightError):
            self._page.wait_for_load_state("domcontentloaded", timeout=10_000)

    def _settle(self) -> None:
        """Wait for the page to stop moving before looking at it.

        Deliberately minimal at this layer: a load-state wait and nothing more.
        Deciding how long to wait for a *particular expected condition* is a
        replay concern, because only replay knows what it is waiting for.
        """
        with contextlib.suppress(PlaywrightError):
            self._page.wait_for_load_state("domcontentloaded", timeout=5_000)

    def _frame_path(self, frame: Frame) -> tuple[str, ...]:
        parts: list[str] = []
        current: Frame | None = frame
        while current is not None:
            parts.append(current.name or ("main" if current.parent_frame is None else "frame"))
            current = current.parent_frame
        return tuple(reversed(parts))

    def _handle(self, ref: str):
        token, _, rest = ref.partition(":")
        frame_index, _, element_index = rest.partition(":")

        # Rejecting a stale ref is the whole reason tokens exist. The element
        # registry is rebuilt on every observation, so an old ref would
        # otherwise resolve happily to whatever now sits at that index -- a
        # silent wrong action, which is far worse than a loud failure.
        if token != self._token or not self._token:
            raise LookupError(
                f"ref {ref!r} is from a different observation than the current "
                f"one ({self._token or 'none'}); observe() before acting"
            )

        frame = self._frames.get(int(frame_index.lstrip("f")))
        if frame is None:
            raise LookupError(f"ref {ref!r} names a frame that is no longer present")
        handle = frame.evaluate_handle(
            "i => (window.__px || [])[i]", int(element_index)
        ).as_element()
        if handle is None:
            raise LookupError(f"ref {ref!r} no longer resolves to an element")
        return handle


@contextlib.contextmanager
def browser_session(
    *,
    headless: bool = True,
    viewport: tuple[int, int] = (1280, 900),
) -> Iterator[BrowserSurface]:
    """Open a browser and yield a surface for it.

    A single context per session, so cookies and the application's session
    survive across navigations -- which is what makes a human takeover of the
    *same* live session possible later on.
    """
    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": viewport[0], "height": viewport[1]}
        )
        page = context.new_page()
        surface = BrowserSurface(page)
        try:
            yield surface
        finally:
            with contextlib.suppress(PlaywrightError):
                context.close()
            with contextlib.suppress(PlaywrightError):
                browser.close()
