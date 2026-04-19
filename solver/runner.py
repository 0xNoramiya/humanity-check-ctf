from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
from pathlib import Path

from playwright.async_api import BrowserContext, async_playwright

from .actions import Action, execute
from .perception import (
    Observation,
    click_play_again,
    locate_verify_checkbox,
    observe,
)
from .policy import Policy, policy_from_env
from .signals import SignalBuffer, is_interesting


async def _prime_session(context: BrowserContext, login_url: str) -> None:
    page = await context.new_page()
    await page.goto(login_url, wait_until="commit", timeout=60_000)
    await page.wait_for_timeout(1200)
    await page.close()


async def _safe_goto(page, url: str) -> None:
    try:
        await page.goto(url, wait_until="commit", timeout=60_000)
    except Exception as e:
        print(f"[solver] goto warning ({url}): {str(e)[:100]}")
    for _ in range(4):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)
            break
        except Exception as e:
            print(f"[solver] load_state warning: {str(e)[:80]}")
    await page.wait_for_timeout(3000)


def _log_line(run_dir: Path, tick: int, obs: Observation, action: Action) -> None:
    entry = {
        "tick": tick,
        "ts": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "score": obs.score_text,
        "game_over": obs.game_over,
        "flag": obs.flag,
        "capture_blank": obs.capture_looks_blank,
        "visible_text": obs.visible_text,
        "signals": obs.signals,
        "action": action.model_dump(exclude_none=True),
    }
    (run_dir / "transcript.jsonl").open("a").write(json.dumps(entry) + "\n")


async def run() -> str | None:
    login_url = os.environ["LOGIN_URL"]
    target_url = os.environ.get("TARGET_URL", "https://verify.b01lersc.tf/")
    headless = os.environ.get("HEADLESS", "false").lower() == "true"
    tick_ms = int(os.environ.get("TICK_MS", "700"))
    max_ticks = int(os.environ.get("MAX_TICKS", "400"))
    log_root = Path(os.environ.get("LOG_DIR", "runs"))

    run_dir = log_root / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[solver] run dir: {run_dir}")

    policy = policy_from_env()
    history: list[str] = []
    last_flag: str | None = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        # No init script — any JS we inject before the page's own code tends to
        # disturb Chromium's compositor pipeline and leads to blank screenshots.
        # We rely solely on CDP Page.captureScreenshot for perception.
        await _prime_session(context, login_url)
        page = await context.new_page()

        # Wire up network/DOM signals — these are our primary perception channel
        # because WSL2 can't reliably screenshot the game's WebGL canvas.
        signals = SignalBuffer()

        def on_req(req):
            if is_interesting(req.url):
                signals.add("req", f"{req.method} {req.url}")

        def on_resp(resp):
            u = resp.url
            if is_interesting(u):
                signals.add("resp", f"{resp.status} {u}")

        page.on("request", on_req)
        page.on("response", on_resp)

        await _safe_goto(page, target_url)

        # Deterministic: locate the checkbox, then click it with real browser
        # input events (page.mouse) so canvas-level handlers fire.
        for attempt in range(8):
            try:
                coords = await locate_verify_checkbox(page)
            except Exception as e:
                print(f"[solver] checkbox locate err: {str(e)[:80]}")
                coords = None
            if coords:
                cx, cy = coords
                print(f"[solver] checkbox at ({cx},{cy}) attempt {attempt}")
                try:
                    await page.mouse.move(cx, cy)
                    await page.mouse.down()
                    await page.wait_for_timeout(40)
                    await page.mouse.up()
                except Exception as e:
                    print(f"[solver] mouse click err: {str(e)[:80]}")
                break
            await page.wait_for_timeout(1000)
        await page.wait_for_timeout(3000)

        for tick in range(max_ticks):
            try:
                obs = await observe(page, signals_text=signals.summarize(seconds=12.0))
            except Exception as e:
                print(f"[solver] observe error: {str(e)[:120]}")
                await page.wait_for_timeout(500)
                continue
            if obs.flag:
                print(f"[solver] FLAG: {obs.flag}")
                (run_dir / "flag.txt").write_text(obs.flag + "\n")
                last_flag = obs.flag
                break
            if obs.game_over:
                print(f"[solver] tick {tick}: game-over, score={obs.score_text!r}")
                (run_dir / f"gameover_{tick}.png").write_bytes(obs.screenshot)
                if not await click_play_again(page):
                    action = Action(type="wait", ms=800, reason="game-over but no button found")
                    await execute(page, action)
                    history.append(f"{action.type}: {action.reason}")
                    continue
                await page.wait_for_timeout(1200)
                continue

            try:
                action = await policy.decide(
                    obs.screenshot,
                    obs.viewport,
                    obs.score_text,
                    history,
                    signals=obs.signals,
                    visible_text=obs.visible_text,
                    capture_blank=obs.capture_looks_blank,
                )
            except Exception as e:
                action = Action(type="wait", ms=500, reason="policy error: " + str(e)[:80])

            print(f"[t{tick:03d}] {action.type:<6} {action.reason[:90]}")
            _log_line(run_dir, tick, obs, action)
            history.append(f"{action.type}: {action.reason[:80]}")
            if tick % 5 == 0:
                (run_dir / f"shot_{tick:03d}.png").write_bytes(obs.screenshot)

            try:
                await execute(page, action, offset=obs.canvas_offset)
            except Exception as e:
                print(f"[solver] action failed: {e}")

            await page.wait_for_timeout(tick_ms)

        await policy.aclose()
        await context.close()
        await browser.close()

    return last_flag
