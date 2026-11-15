# chotuBot

A personal AI chat app that runs on your own computer. You bring your own model API key; conversations are stored locally.

## Features

- Chat with a tool-using agent (FastAPI + LangGraph + LangChain)
- Two read-only demo tools: project status and note search, with the tool calls shown in the chat
- Models via OpenRouter (default) or NVIDIA NIM (free-tier testing), plus an offline demo mode
- Conversation history in a local SQLite file, with a sidebar to reopen past chats
- Usage and cost tracking with a monthly budget limit (estimates only, not billing totals)
- Responsive React + TypeScript UI (works from 360px wide)
- Local only: binds to `127.0.0.1`; your API key never reaches the browser
- Optional LangSmith tracing, off by default and redacted when on

## Run locally

Requires Python 3.11+ and Node 20+. From the repo root (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cd frontend; npm install; npm run build; cd ..
Copy-Item .env.example .env
```

Edit `.env` (it is git-ignored) and choose a provider:

| Provider | Settings |
|---|---|
| OpenRouter (default) | `OPENROUTER_API_KEY=...` |
| NVIDIA NIM | `CHOTU_PROVIDER=nim`, `NVIDIA_API_KEY=...`, `CHOTU_DEFAULT_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b` |
| Offline demo (no key) | `CHOTU_PROVIDER=demo` |

Also set `CHOTU_USD_TO_INR` to the current rate (used for the budget). Allowed models and prices live in `config/models.json`.

Start the app and open <http://127.0.0.1:8000>:

```powershell
python -m app
```

Try "What is the status of atlas?" or "Search my notes for budget".

For UI development with hot reload, keep the backend running and use `cd frontend; npm run dev` (<http://127.0.0.1:5173>).

## Tests

```powershell
python -m pytest                           # backend (mocked, no network, no cost)
cd frontend; npm test; npm run typecheck   # frontend
python -m evals.evaluators.run             # golden-case evaluation with recorded model responses
```

## Notes

- Costs are local estimates and may differ from what your provider bills.
- History is stored in `data/chotubot.sqlite3`; delete it to erase all chats.
- Free-tier models can be slow or unavailable; pick another in Settings if a request times out.
