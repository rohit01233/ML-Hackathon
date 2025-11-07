Nyay Sahayak MVP

1. Put source docs in ./data/ (txt or md). Include metadata lines at top:
   source_name:...
   url:...
   date_published:...
   jurisdiction:...
   doc_type:...

2. Build index:
   python backend/preprocess.py

3. Start backend:
   Windows (CMD) — set OPENAI_API_KEY for current session and start backend:

   ```
   set OPENAI_API_KEY=your_openai_api_key_here
   .venv\Scripts\activate
   uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
   ```

   Windows (PowerShell) — set for session or persistently and start backend:

   ```
   # session (temporary)
   $env:OPENAI_API_KEY = "your_openai_api_key_here"
   . .venv\Scripts\Activate.ps1
   uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

   # persistent (will apply to new shells)
   setx OPENAI_API_KEY "your_openai_api_key_here"
   ```

   Notes:

   - On Windows, use setx to persist; you must restart your shell to see the value.
   - On Linux/macOS, export OPENAI_API_KEY="your_openai_api_key_here" before starting uvicorn.
   - If you prefer not to set env vars, you can create a wrapper script that sets the variable then starts uvicorn (not included by default for security reasons).

   export OPENAI_API_KEY=your_key (Windows PowerShell: $env:OPENAI_API_KEY="your_key")
   uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

Quick fixes if you see: {"error":"OPENAI_API_KEY not set in environment. Export it and restart the backend."}

1. Temporary (current shell)

- CMD:
  set OPENAI_API_KEY=sk-your_key_here
  .venv\Scripts\activate
  uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
- PowerShell:
  $env:OPENAI_API_KEY = "sk-your_key_here"
  . .venv\Scripts\Activate.ps1
  uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

2. Persistent (Windows)

- Run once:
  setx OPENAI_API_KEY "sk-your_key_here"
  Restart your terminal.

3. Local .env (recommended for dev)

- Copy .env.example -> .env and set:
  OPENAI_API_KEY=sk-your_key_here
- Then activate venv and start backend.

4. Set key at runtime (no restart)

- For quick local testing you can POST the key to the running backend (loopback only):
  curl -X POST http://127.0.0.1:8000/set_api_key -H "Content-Type: application/json" -d '{"key":"sk-your_key_here"}'
- This is temporary (in-process) and for local dev only.

Security note

- If you accidentally exposed a key (e.g. pasted it in chat or committed it), rotate/revoke it immediately in the OpenAI dashboard and replace it with a new key.

4. Start frontend:
   streamlit run frontend/app.py

This demo uses sentence-transformers for embeddings + FAISS and OpenAI for generation.

Optional: use a local .env file

- Copy .env.example -> .env in project root and set:
  OPENAI_API_KEY=sk-your_real_openai_key_here
- The backend will automatically load .env at startup (python-dotenv).
- Do NOT commit your .env file to git.
