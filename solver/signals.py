"""Rolling buffers of network + DOM events we watch in lieu of visual capture.

WSL2's Chromium compositor refuses to let us screenshot the game's WebGL canvas,
so we listen to the game instead. Every asset it fetches (click.png, type.png,
mine.png, flag.png, move.png, etc.) is a hint about the active mini-game. Audio
cues (bad3.mp3, lose4.mp3) signal outcomes. We drip-feed this into the LLM.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class SignalBuffer:
    max_events: int = 120
    events: deque = field(default_factory=lambda: deque(maxlen=120))

    def add(self, kind: str, detail: str) -> None:
        self.events.append((time.monotonic(), kind, detail[:200]))

    def recent(self, seconds: float = 10.0) -> list[tuple[float, str, str]]:
        cutoff = time.monotonic() - seconds
        return [e for e in self.events if e[0] >= cutoff]

    def summarize(self, seconds: float = 10.0) -> str:
        recent = self.recent(seconds)
        if not recent:
            return "(no recent events)"
        # Compact: count occurrences of identical (kind, tail-url) pairs, keep order.
        counts: dict[tuple[str, str], int] = {}
        order: list[tuple[str, str]] = []
        for _ts, kind, detail in recent:
            key = (kind, _tail(detail))
            if key not in counts:
                order.append(key)
            counts[key] = counts.get(key, 0) + 1
        lines = []
        for kind, det in order[-25:]:
            n = counts[(kind, det)]
            lines.append(f"{kind}{'*' + str(n) if n > 1 else ''}: {det}")
        return "\n".join(lines)


def _tail(s: str) -> str:
    """Return just the filename/path tail of a URL, stripped of query string."""
    # detail often looks like "GET https://host/a/b/c.png?q=1"
    url_start = s.find("http")
    if url_start == -1:
        return s[:80]
    url = s[url_start:].split(" ", 1)[0].split("?", 1)[0].split("#", 1)[0]
    # take the path after host
    slash = url.find("/", 8)  # skip past "https://"
    if slash == -1:
        return url[:80]
    path = url[slash:] or "/"
    # keep last 2 segments for context
    segs = [seg for seg in path.split("/") if seg]
    if len(segs) >= 2:
        return "/".join(segs[-2:])[:80]
    return path[:80]


# Paths whose basename is a strong hint about the active mini-game.
INTERESTING_SUBSTRINGS = (
    ".png",
    ".webp",
    ".gif",
    ".mp3",
    ".wav",
    ".mp4",
    "/api/",
    "/verify",
    "/submit",
    "/score",
    "/game",
    "/round",
)

# Assets that load on every page visit — noisy, not useful mini-game hints.
IGNORED_SUBSTRINGS = (
    "/leaderboard",
    "/logo.png",
    "/background3.webp",
    "/support/",
    "/favicon",
    "/_next/",
    ".ttf",
    ".otf",
    ".woff",
    ".css",
)


def is_interesting(url: str) -> bool:
    if any(tok in url for tok in IGNORED_SUBSTRINGS):
        return False
    return any(tok in url for tok in INTERESTING_SUBSTRINGS)
