# chotuBot

A personal AI chat app that runs on your own computer. You bring your own model API key; conversations are stored locally.

## Quick start with an AI coding assistant

Open an empty folder in your AI editor (Claude Code, Cursor, Copilot agent mode, etc.) and paste this prompt:

````text
Set up and run the chotuBot project for me, so I do not have to do anything by hand.

1. Clone https://github.com/vKaran25/chotuBot.git into the current folder and cd into it.
2. Check that Python 3.11+ and Node 20+ are installed. If not, tell me what to install and stop.
3. Detect my operating system and shell and use matching commands. Create a virtual environment in .venv, activate it, and run: pip install -e ".[dev]"
4. In the frontend folder run: npm install, then npm run build.
5. Copy .env.example to .env.
6. Ask me whether I have an NVIDIA NIM API key (NVIDIA_API_KEY) or want the offline demo (CHOTU_PROVIDER=demo, no key). Set CHOTU_PROVIDER in .env accordingly (nim or demo).
7. Do NOT ask me to paste an API key into this chat and never print one. Tell me to open .env myself, paste the key on the right line, and save; wait until I say it is done. Then check only that the line is non-empty, without showing its value.
8. Run the backend with: python -m app (keep it running in the background) and confirm http://127.0.0.1:8765/api/health returns ok.
9. Tell me to open http://127.0.0.1:8765, then give me these brief instructions for using it:
   - Type in the box at the bottom and press Enter (Shift+Enter for a new line).
   - Try "What is the status of atlas?" or "Search my notes for security", and click the tool entry under the answer to see what it ran.
   - "New chat" in the sidebar starts a fresh conversation; click an earlier chat to reopen it.
   - To delete a chat, hover it in the sidebar, click the ... button, choose Delete and confirm.
   - The Settings button (top right) shows the key status and lets me pick the model.
   - To stop the app, press Ctrl+C in the terminal where it is running.

Only change files in this project folder. Do not commit, push, or send my keys anywhere.
````

## Features

- Chat with a tool-using agent (FastAPI + LangGraph + LangChain)
- Two read-only demo tools: project status and note search, with the tool calls shown in the chat
- Models via NVIDIA NIM (free tier works), plus an offline demo mode that needs no key
- Conversation history in a local SQLite file, with a sidebar to reopen or delete past chats
- Per-request usage tracking (tokens, latency, tool calls)
- Responsive React + TypeScript UI (works from 360px wide)
- Local only: binds to `127.0.0.1`; your API key never reaches the browser
- Optional LangSmith tracing, off by default and redacted when on

## Using the app

Once the server is running, open <http://127.0.0.1:8765>:

- **Chat:** type in the box at the bottom and press Enter (Shift+Enter adds a new line). Answers appear in the main area.
- **Try the demo tools:** ask "What is the status of atlas?" or "Search my notes for security". Click the tool entry under the answer to see what it ran and what it returned.
- **New chat / past chats:** use **New chat** in the sidebar. Click any earlier chat to reopen it (on a phone, open the sidebar with the menu button).
- **Delete a chat:** hover a chat in the sidebar, click the **...** button, choose **Delete** and confirm. This permanently removes it from history and from the assistant's memory.
- **Settings:** the **Settings** button (top right) shows whether your key is configured, lets you pick the model, and lists recent requests with tokens and latency.
- **Stop the app:** press Ctrl+C in the terminal running `python -m app`. Your chats are kept for next time.

### Ask questions about your own documents

- **The + button** in the message box: **Add a file** uploads a text PDF, Markdown or TXT file (max 10 MB) to the current workspace; **Manage documents** lets you pick or create a workspace and reindex or delete files. Files are indexed locally with a small embedding model (`BAAI/bge-small-en-v1.5`, downloaded once, about 67 MB, into `data/models/`) and a FAISS index.
- **Allow sharing:** with NVIDIA NIM, a document question sends the few matching excerpts to NVIDIA. That is off by default for each workspace; turn on **Allow sharing excerpts with the cloud model** in Manage documents (you can turn it off again). The offline demo provider needs no consent.
- **Use my documents:** choose it from the + menu (a "Documents: …" chip appears; × turns it off). The answer cites its sources as `[1]`, `[2]`…; click a source to see the excerpt, file name and page/section/lines. If nothing in the workspace matches, the app says so instead of guessing. With the box unticked, chat works exactly as before.
- Documents are stored next to the database in `data/chotubot.rag/`; deleting a document removes its file, text and index entries.

## Run locally

Requires Python 3.11+ and Node 20+. From the repo root (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cd frontend; npm install; npm run build; cd ..
Copy-Item .env.example .env
```

Edit `.env` (it is git-ignored) and set `NVIDIA_API_KEY=...` (get a key at build.nvidia.com). The default model is `nvidia/nemotron-3.5-lightning-30b-a3b`; the allowed models are listed in `config/models.json`.

No key? Set `CHOTU_PROVIDER=demo` for an offline model that only exercises the UI and tools.

Start the app and open <http://127.0.0.1:8765>:

```powershell
python -m app
```

For UI development with hot reload, keep the backend running and use `cd frontend; npm run dev` (<http://127.0.0.1:5173>).

## Tests

```powershell
python -m pytest                           # backend incl. document features (mocked, no network, no cost)
cd frontend; npm test; npm run typecheck   # frontend
python -m evals.evaluators.run             # golden-case evaluation with recorded model responses
```

## Notes

- Your prompts are sent to NVIDIA when using the `nim` provider; keep sensitive data out of test chats.
- History is stored in `data/chotubot.sqlite3`; delete it to erase all chats. Delete `data/chotubot.rag/` as well to erase all uploaded documents.
- Start the app from a clean terminal: a leftover `CHOTU_PROVIDER=demo` or `CHOTU_EMBEDDER=hash` environment variable overrides `.env`.
- Free-tier models can be slow or unavailable; pick another in Settings if a request times out.
