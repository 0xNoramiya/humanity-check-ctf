from __future__ import annotations

from typing import Literal, Optional

from playwright.async_api import Page
from pydantic import BaseModel, Field


class Action(BaseModel):
    type: Literal["click", "double_click", "key", "type", "drag", "wait", "scroll", "noop"]
    x: Optional[int] = None
    y: Optional[int] = None
    x2: Optional[int] = None
    y2: Optional[int] = None
    key: Optional[str] = None
    text: Optional[str] = None
    ms: Optional[int] = Field(default=None, ge=0, le=5000)
    dy: Optional[int] = None
    reason: str = "(no reason given)"


async def execute(page: Page, action: Action, offset: tuple[int, int] = (0, 0)) -> None:
    ox, oy = offset
    t = action.type
    if t == "click":
        await page.mouse.click(_i(action.x) + ox, _i(action.y) + oy)
    elif t == "double_click":
        await page.mouse.dblclick(_i(action.x) + ox, _i(action.y) + oy)
    elif t == "key":
        await page.keyboard.press(action.key or "Enter")
    elif t == "type":
        await page.keyboard.type(action.text or "", delay=30)
    elif t == "drag":
        await page.mouse.move(_i(action.x) + ox, _i(action.y) + oy)
        await page.mouse.down()
        await page.mouse.move(_i(action.x2) + ox, _i(action.y2) + oy, steps=14)
        await page.mouse.up()
    elif t == "scroll":
        await page.mouse.wheel(0, action.dy or 0)
    elif t == "wait":
        await page.wait_for_timeout(action.ms or 300)
    elif t == "noop":
        pass


def _i(v: Optional[int]) -> int:
    if v is None:
        raise ValueError("coordinate required")
    return int(v)
