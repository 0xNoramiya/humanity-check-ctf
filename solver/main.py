from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

from .runner import run


def main() -> int:
    load_dotenv()
    flag = asyncio.run(run())
    if flag:
        print(f"\n=== FLAG ===\n{flag}\n============")
        return 0
    print("\n[solver] no flag captured; see transcript.jsonl for what the AI did.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
