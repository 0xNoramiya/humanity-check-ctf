from __future__ import annotations

import asyncio
import base64
import re
from dataclasses import dataclass

from playwright.async_api import Page

FLAG_RE = re.compile(r"(?:bctf|b01lers|flag)\{[^}\s]{3,200}\}", re.IGNORECASE)


@dataclass
class Observation:
    screenshot: bytes
    viewport: tuple[int, int]  # image dimensions the LLM sees
    score_text: str
    game_over: bool
    flag: str | None
    canvas_offset: tuple[int, int] = (0, 0)  # add to LLM coords to get viewport coords
    canvas_size: tuple[int, int] | None = None  # logical size of the capture
    signals: str = ""  # textual summary of recent network/DOM events
    visible_text: str = ""  # plain text of visible DOM elements
    capture_looks_blank: bool = False  # hint: treat text signals as primary


_VISIBLE_TEXT_JS = """
() => {
  const out = { body: '', visibleGameOver: false, score: null };
  const effectiveOpacity = (el) => {
    let op = 1; let n = el;
    while (n && n !== document.body) {
      const a = parseFloat(getComputedStyle(n).opacity || '1');
      if (!isNaN(a)) op *= a;
      if (op < 0.01) return op;
      n = n.parentElement;
    }
    return op;
  };
  const vw = window.innerWidth, vh = window.innerHeight;
  const onScreen = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    // Element rect must overlap the viewport.
    if (r.right < 0 || r.bottom < 0) return false;
    if (r.left > vw || r.top > vh) return false;
    return true;
  };
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    if (effectiveOpacity(el) < 0.05) return false;
    if (!onScreen(el)) return false;
    return true;
  };
  const parts = [];
  const leaves = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,button,a,label,input,textarea,li,span'));
  for (const el of leaves) {
    const t = (el.textContent || el.value || el.placeholder || '').trim();
    if (!t || t.length > 200) continue;
    if (!visible(el)) continue;
    parts.push(`${el.tagName.toLowerCase()}: ${t}`);
    if (/^game\\s*over$/i.test(t) || /didn't\\s*score\\s*enough/i.test(t)) {
      out.visibleGameOver = true;
    }
    const m = t.match(/\\bscore[^0-9]{0,10}(\\d{1,9})\\b/i);
    if (m && !out.score) out.score = m[1];
  }
  out.body = parts.slice(0, 30).join('\\n');
  return out;
}
"""


async def observe(page: Page, signals_text: str = "") -> Observation:
    # Primary: CDP viewport screenshot with captureBeyondViewport. This works
    # in headless where Chromium's compositor can't always flush WebGL content
    # through the default screenshot path.
    png, offset, size = (b"", (0, 0), None)
    shot = await _safe_viewport_screenshot(page)
    if shot and not _is_mostly_black(shot):
        png, offset, size = shot, (0, 0), None

    if not png:
        cap = await _capture_canvas_with_rect(page)
        if cap is not None:
            png, offset, size = cap

    if not png:
        mirror = await _capture_mirror(page)
        if mirror is not None:
            png, offset, size = mirror

    if not png:
        # Absolute fallback so the loop can continue.
        png = shot or base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        )

    vp = page.viewport_size or {"width": 1280, "height": 800}
    viewport = size if size is not None else (vp["width"], vp["height"])

    try:
        info = await asyncio.wait_for(page.evaluate(_VISIBLE_TEXT_JS), timeout=4.0)
    except Exception as e:
        print(f"[perception] DOM probe failed: {str(e)[:120]}")
        info = {"body": "", "visibleGameOver": False, "score": None}
    body_text = info.get("body", "")
    flag_m = FLAG_RE.search(body_text)
    flag = flag_m.group(0) if flag_m else None
    score_text = f"score={info['score']}" if info.get("score") else _extract_score(body_text)
    capture_blank = _is_mostly_black(png) or _looks_uniform(png)
    return Observation(
        screenshot=png,
        viewport=viewport,
        score_text=score_text,
        game_over=bool(info.get("visibleGameOver")) or flag is not None,
        flag=flag,
        canvas_offset=offset,
        canvas_size=size,
        signals=signals_text,
        visible_text=body_text[:500],
        capture_looks_blank=capture_blank,
    )


def _looks_uniform(png: bytes) -> bool:
    """Cheap heuristic: uniform-color PNGs compress to a very small file."""
    return 0 < len(png) < 22_000  # anything under ~22KB at 1280x800 is suspect


def _extract_score(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("score"):
            return line[:80]
    return "unknown"


async def _capture_mirror(
    page: Page,
) -> tuple[bytes, tuple[int, int], tuple[int, int]] | None:
    """Read the solver's mirror canvas (kept current by our rAF hook)."""
    try:
        res = await page.evaluate(
            """
            () => {
              if (typeof window.__solverSnapshot !== 'function') return null;
              const url = window.__solverSnapshot();
              if (!url) return null;
              const canvases = Array.from(document.querySelectorAll('canvas'))
                .filter(c => c.id !== '__solver_mirror' && c.width > 0 && c.height > 0);
              canvases.sort((a, b) => (b.width * b.height) - (a.width * a.height));
              const c = canvases[0];
              if (!c) return null;
              const r = c.getBoundingClientRect();
              return {
                url,
                offsetX: Math.round(r.left),
                offsetY: Math.round(r.top),
                rectW: Math.round(r.width),
                rectH: Math.round(r.height),
              };
            }
            """
        )
    except Exception:
        return None
    if not res or not res.get("url", "").startswith("data:image/png;base64,"):
        return None
    try:
        png = base64.b64decode(res["url"].split(",", 1)[1])
    except Exception:
        return None
    return png, (res["offsetX"], res["offsetY"]), (res["rectW"], res["rectH"])


async def _capture_canvas_with_rect(
    page: Page,
) -> tuple[bytes, tuple[int, int], tuple[int, int]] | None:
    """PNG + (offsetX, offsetY) to translate into viewport + canvas logical (w, h)."""
    res = await page.evaluate(
        """
        () => {
          const canvases = Array.from(document.querySelectorAll('canvas'));
          if (canvases.length === 0) return null;
          canvases.sort((a, b) => {
            const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
            return rb.width * rb.height - ra.width * ra.height;
          });
          const c = canvases[0];
          const r = c.getBoundingClientRect();
          let url = null;
          try { url = c.toDataURL('image/png'); } catch (e) { return null; }
          return {
            url,
            offsetX: Math.round(r.left),
            offsetY: Math.round(r.top),
            rectW: Math.round(r.width),
            rectH: Math.round(r.height),
          };
        }
        """
    )
    if not res or not res.get("url", "").startswith("data:image/png;base64,"):
        return None
    try:
        png = base64.b64decode(res["url"].split(",", 1)[1])
    except Exception:
        return None
    return png, (res["offsetX"], res["offsetY"]), (res["rectW"], res["rectH"])


_CDP_SESSIONS: dict[int, object] = {}


async def _cdp(page: Page):
    key = id(page)
    s = _CDP_SESSIONS.get(key)
    if s is None:
        s = await page.context.new_cdp_session(page)
        _CDP_SESSIONS[key] = s
    return s


async def _safe_viewport_screenshot(page: Page) -> bytes:
    """Use CDP Page.captureScreenshot — doesn't wait for animations to settle.

    Wraps every capture path in an asyncio timeout so a hung renderer can't
    stall the main solver loop.
    """
    try:
        session = await _cdp(page)
        result = await asyncio.wait_for(
            session.send(
                "Page.captureScreenshot",
                {"format": "png", "fromSurface": True, "captureBeyondViewport": True},
            ),
            timeout=4.0,
        )
        return base64.b64decode(result["data"])
    except Exception as e:
        print(f"[perception] CDP screenshot failed: {str(e)[:120]}")
        try:
            return await asyncio.wait_for(
                page.screenshot(type="png", full_page=False, timeout=3000),
                timeout=4.0,
            )
        except Exception as e2:
            print(f"[perception] viewport fallback failed: {str(e2)[:80]}")
            return base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
            )


def _is_mostly_black(png: bytes) -> bool:
    """Very small PNG => uniform color, treat as empty/failed capture."""
    if len(png) < 1500:
        return True
    return False


async def click_play_again(page: Page) -> bool:
    for selector in [
        "button:has-text('Play Again')",
        "button:has-text('Retry')",
        "button:has-text('Try Again')",
    ]:
        btn = page.locator(selector).first
        try:
            if await btn.count() and await btn.is_visible():
                await btn.click(force=True)
                return True
        except Exception:
            continue
    return False


async def locate_verify_checkbox(page: Page) -> tuple[int, int] | None:
    """Return (cx, cy) viewport coords of the 'Verify you are human' checkbox, or None."""
    res = await page.evaluate(
        """
        () => {
          const labels = Array.from(document.querySelectorAll('div,span,p'))
            .filter(e => /verify\\s+you\\s+are\\s+human/i.test((e.textContent || '').trim()));
          if (labels.length === 0) return null;
          let widget = null;
          for (const lbl of labels) {
            let n = lbl;
            for (let i = 0; i < 5 && n; i++) {
              const r = n.getBoundingClientRect();
              if (r.width > 200 && r.width < 500 && r.height > 40 && r.height < 120) {
                widget = n; break;
              }
              n = n.parentElement;
            }
            if (widget) break;
          }
          if (!widget) return null;
          const candidates = Array.from(widget.querySelectorAll('div'));
          let best = null; let bestDx = Infinity;
          const wr = widget.getBoundingClientRect();
          for (const el of candidates) {
            const r = el.getBoundingClientRect();
            if (r.width < 10 || r.width > 40) continue;
            if (r.height < 10 || r.height > 40) continue;
            const dx = Math.abs(r.left - wr.left);
            if (dx < bestDx) { bestDx = dx; best = el; }
          }
          if (!best) return null;
          const r = best.getBoundingClientRect();
          return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
        }
        """
    )
    if not res:
        return None
    return (res["x"], res["y"])
