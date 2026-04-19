# humanity-check-ctf

Bit for the b01lers CTF "humanity check" challenge (`verify.b01lersc.tf`) — a
fake Cloudflare Turnstile that actually runs a run of WarioWare-style mini-games.
Score high enough, get the flag. This repo is a vision-driven AI player: every
tick it screenshots the game, asks a multimodal LLM via OpenRouter what to do,
and forwards the model's structured action back through Playwright.

The joke: the humanity check gets solved by literally-not-a-human.

## Layout

```
solver/
  runner.py       # Playwright session, main loop, Play Again handling
  perception.py   # screenshot + DOM probe + visible-text extraction
  policy.py       # OpenRouter call, JSON-mode structured output
  prompts.py      # system prompt + per-tick user prompt
  actions.py      # Pydantic Action schema, Playwright executor
  signals.py      # rolling buffer of network events (asset requests, etc.)
  main.py         # entrypoint
requirements.txt
.env.example
```

Transcripts land in `runs/<timestamp>/transcript.jsonl`, one line per tick, with
the model's `reason` field — that file is the output you actually want to read.

## Setup (Windows)

Recommended: run from a real Chrome (not WSL). Playwright + WSL2 + WebGL
canvas = blank screenshots; the LLM can't see the game. From a normal Windows
install everything works.

```powershell
# 1. Clone
git clone https://github.com/0xNoramiya/humanity-check-ctf.git
cd humanity-check-ctf

# 2. Python 3.11+ recommended. Create a venv.
py -m venv .venv
.venv\Scripts\activate

# 3. Install deps + browser.
pip install -r requirements.txt
playwright install chromium

# 4. Configure .env (copy from example).
copy .env.example .env
notepad .env
```

Edit `.env`:

- `OPENROUTER_API_KEY` — your key (starts with `sk-or-v1-`).
- `OPENROUTER_MODEL` — default `google/gemini-2.5-flash` is a solid vision
  model. `anthropic/claude-sonnet-4-6` is the premium pick if Gemini keeps
  missing.
- `LOGIN_URL` — your one-shot login URL from the CTF (the `?token=…` flavor).
- `HEADLESS=false` — watch it play. `true` if you want to grind in the
  background.
- `TICK_MS` — delay between actions (700 ms feels right).
- `MAX_TICKS` — hard ceiling per run.

Run it:

```powershell
python -m solver.main
```

Chromium pops up, the AI clicks the "Verify you are human" checkbox, then
starts guessing. The flag — if it hits the score threshold — lands in
`runs\<timestamp>\flag.txt`.

## Setup (macOS / Linux)

Same as Windows, with the obvious command swaps:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
$EDITOR .env
python -m solver.main
```

## WSL2 caveat

If you must run in WSL, know that Chromium's screenshot pipeline in WSL2
(even with WSLg) can't reliably capture WebGL content on this site — captures
come back uniform near-black. The solver will still run and log the model's
reasoning, but it won't win. Works fine from the Windows host itself.

## What the transcript looks like

```
t00 click Verify checkbox to begin.
t01 key   Space — 'click.png' just loaded, hope that starts something.
t02 click type.png is in the signals; typing 'human' into the middle of the canvas.
t03 click miss.png loaded, I missed; hitting something else.
...
```

That transcript is the bit.
