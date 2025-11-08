import os, json, threading, time
# Load .env from project root if present (allows local .env with OPENAI_API_KEY)
from typing import Optional
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, Request
from pydantic import BaseModel
import backend.retrieval as retrieval_module
import requests
import faiss  # used later in citation mapping
import logging
import smtplib
from email.message import EmailMessage
from datetime import datetime
from pathlib import Path
import hashlib
import uuid

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

# Add a short disclaimer returned when the LLM produces no usable output
DISCLAIMER = (
    "No answer returned.\n\n"
    "This tool provides information, not legal advice. For urgent/legal representation "
    "contact a licensed lawyer or your local police. Nyay Sahayak is not a substitute for "
    "professional legal counsel."
)

app = FastAPI(title="Nyay Sahayak - RAG backend")
EMBED_MODEL = "all-MiniLM-L6-v2"

def build_context(hits):
    blocks = []
    for idx, h in enumerate(hits):
        meta = h.get("meta", {})
        blocks.append(f"[DOC id={h['id']} | source={meta.get('source_name','')} | jurisdiction={meta.get('jurisdiction','')}]\\n{h['text']}")
    return "\\n\\n".join(blocks)

with open(os.path.join(os.path.dirname(__file__), "..", "templates", "prompt.txt"), "r", encoding="utf-8") as f:
    PROMPT_TEMPLATE = f.read()

# moved instantiation to startup to avoid long import-time work
# _embed_model will reference retrieval_module._MODEL once resources load
_embed_model = None

# resource status flags
RESOURCE_LOADED = False
RESOURCE_ERROR = None

@app.get("/health")
def health():
    """
    Lightweight health endpoint so the frontend can check backend availability quickly.
    """
    # provide resource / embed readiness so UI can show meaningful status
    return {
        "status": "ok",
        "message": "Backend up. Ensure OPENAI_API_KEY is set if using LLM.",
        "resources_loaded": RESOURCE_LOADED,
        "resource_error": str(RESOURCE_ERROR) if RESOURCE_ERROR else None,
        "embed_model_loaded": _embed_model is not None,
        "retriever_model_loaded": getattr(retrieval_module, "_MODEL", None) is not None
    }

@app.get("/ready")
def ready():
    """Readiness probe: true only when resources finished loading successfully."""
    if RESOURCE_LOADED:
        return {"ready": True}
    if RESOURCE_ERROR:
        return {"ready": False, "error": str(RESOURCE_ERROR)}
    return {"ready": False, "message": "Resources still loading"}

class QueryIn(BaseModel):
    user_text: str
    jurisdiction: str = None

# Add an optional sentence-split helper that prefers nltk when available,
# but falls back to a lightweight regex-based splitter if nltk isn't installed.
def _split_into_sentences(text: str):
	# prefer nltk if present
	try:
		import nltk as _nltk
		from nltk.tokenize import sent_tokenize
		# If punkt is missing, try to download silently (best-effort)
		try:
			_nltk.data.find("tokenizers/punkt")
		except Exception:
			try:
				_nltk.download("punkt", quiet=True)
			except Exception:
				# ignore download errors; will let sent_tokenize raise if unusable
				pass
		return sent_tokenize(text)
	except Exception:
		# fallback: split on sentence-ending punctuation and newlines
		import re
		if not text or not text.strip():
			return []
		# split by punctuation (.!?), keep abbreviations naive handling
		parts = re.split(r'(?<=[\.\!\?])\s+', text.strip())
		# also split long newline blocks
		sents = []
		for p in parts:
			for line in p.splitlines():
				line = line.strip()
				if line:
					sents.append(line)
		# final cleanup: if nothing found, return the whole text
		return sents if sents else [text.strip()]

@app.post("/query")
def query(q: QueryIn):
    # quick guard to help debug: missing API key
    # If OPENAI_API_KEY is missing we no longer fail outright.
    # We will log a warning and later return a retrieval-only fallback answer.
    if not OPENAI_API_KEY:
        logging.getLogger("uvicorn").warning("OPENAI_API_KEY not set; will return retrieval-only fallback answer.")

    # If index/embeddings still building, return an informative payload
    if not RESOURCE_LOADED:
        return {"error": "Backend resources (index/embeddings) are still loading. Try again in a few moments.", "resources_loaded": RESOURCE_LOADED, "resource_error": str(RESOURCE_ERROR) if RESOURCE_ERROR else None}

    # Use retrieval_module.retrieve (ensures single module manages index/model)
    hits = retrieval_module.retrieve(q.user_text, top_k=50, rerank_top=6, jurisdiction=q.jurisdiction)
    if not hits:
        return {"error": "No passages found in index. Run preprocess or add data."}
    context = build_context(hits)
    prompt = PROMPT_TEMPLATE.replace("{CONTEXT}", context).replace("{USER_TEXT}", q.user_text)

    # Fallback behavior: if OPENAI_API_KEY is not set, return the top retrieved passages as the answer
    if not OPENAI_API_KEY:
        # Choose top N passages to return (keep some context)
        top_hits = hits[:3]
        composed = "LLM API key not set; returning top retrieved passages as guidance. To get a synthesized roadmap, set OPENAI_API_KEY in environment or use /set_api_key.\n\n"
        for idx, h in enumerate(top_hits, start=1):
            composed += f"Source {idx} (id={h['id']}, score={h.get('score'):.3f}):\n{h['text']}\n\n---\n\n"
        citations = [{"sentence": None, "citation": {"id": h["id"], "source": h["meta"].get("source_name",""), "score": h.get("score")}} for h in top_hits]
        return {"answer": composed, "citations": citations, "used_passages": top_hits, "note": "OPENAI_API_KEY not set; set it for LLM-generated synthesis (see README/.env or use /set_api_key for local testing)."}

    # call OpenAI chat completion
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    body = {
        "model": "gpt-4o-mini" if False else "gpt-4o" if False else "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": "You are Nyay Sahayak. Use ONLY the provided context to answer."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "max_tokens": 800
    }
    resp = requests.post(OPENAI_CHAT_URL, headers=headers, json=body)
    if resp.status_code != 200:
        return {"error": "LLM call failed", "details": resp.text}
    j = resp.json()
    # Safely extract model content; return disclaimer if empty/missing
    try:
        ans = j["choices"][0]["message"].get("content", "") if isinstance(j.get("choices"), list) else ""
        ans = (ans or "").strip()
    except Exception:
        ans = ""

    if not ans:
        # Return raw disclaimer message when model produced no usable answer.
        return {"answer": None, "raw_answer": DISCLAIMER, "citations": [], "used_passages": hits}
    # citation mapping: split into sentences and map each to best hit by embedding similarity
    # use the robust helper (uses nltk if present, otherwise regex fallback)
    try:
        sents = _split_into_sentences(ans)
    except Exception:
        # last-resort fallback
        sents = [s.strip() for s in ans.split("\\n") if s.strip()]
    hit_texts = [h["text"] for h in hits]
    # ensure embed model present
    # Prefer the model loaded by the retriever to avoid duplicate downloads
    model_for_citation = _embed_model or getattr(retrieval_module, "_MODEL", None)
    if model_for_citation is None:
        return {"error": "Embedding model not available. Check backend logs for resource load errors.", "resource_error": str(RESOURCE_ERROR) if RESOURCE_ERROR else None}
    hit_embs = model_for_citation.encode(hit_texts, convert_to_numpy=True)
    sent_embs = model_for_citation.encode(sents, convert_to_numpy=True)
    import numpy as np
    faiss.normalize_L2(hit_embs)
    faiss.normalize_L2(sent_embs)
    sims = (sent_embs @ hit_embs.T)
    citations = []
    for i, row in enumerate(sims):
        jdx = int(row.argmax())
        score = float(row[jdx])
        if score < 0.55:
            citations.append({"sentence": sents[i], "citation": "No confirmable source found"})
        else:
            citations.append({"sentence": sents[i], "citation": {"id": hits[jdx]["id"], "source": hits[jdx]["meta"].get("source_name",""), "score": score}})
    return {"answer": ans, "citations": citations, "used_passages": hits}

# new: load resources & embedding model at app startup
@app.on_event("startup")
def startup_event():
    """
    Start a background thread that builds the retriever index and loads the embedding model.
    This keeps the HTTP server reachable immediately and allows the UI to poll /health or /ready.
    """
    global RESOURCE_LOADED, RESOURCE_ERROR, _embed_model
    logging.getLogger("uvicorn").info("Startup: scheduling retrieval resources and embedding model load in background...")

    def _background_load():
        global RESOURCE_LOADED, RESOURCE_ERROR, _embed_model
        try:
            # Try to import nltk but do not treat missing nltk as a fatal error.
            try:
                import nltk as _nltk  # optional
                logging.getLogger("uvicorn").info("nltk is available and will be used for sentence tokenization.")
            except Exception:
                _nltk = None
                logging.getLogger("uvicorn").warning("nltk not available — using fallback sentence splitter.")

            # build/load retriever index (may download model & take time)
            retrieval_module.load_resources()
            # reuse retriever's model for citation encoding
            _embed_model = getattr(retrieval_module, "_MODEL", None)
            RESOURCE_LOADED = True
            RESOURCE_ERROR = None
            logging.getLogger("uvicorn").info("Resources and embedding model loaded successfully (background).")
        except Exception as e:
            RESOURCE_LOADED = False
            RESOURCE_ERROR = e
            logging.getLogger("uvicorn").exception("Failed to load resources in background: %s", e)

    t = threading.Thread(target=_background_load, daemon=True)
    t.start()

# small helper: only accept API-key set requests from loopback for local dev
def _is_local_request(req: Request) -> bool:
	# req.client may be None in some test environments; guard accordingly
	try:
		host = req.client.host
	except Exception:
		return False
	return host in ("127.0.0.1", "::1", "localhost")

# Add this endpoint so you can POST {"key":"sk-..."} from your local machine to set the key in the running process.
@app.post("/set_api_key")
def set_api_key(payload: dict, request: Request):
	"""
	Set OPENAI_API_KEY in process (useful for local testing). Only accepts requests from loopback.
	Not persistent — for persistence add the key to .env or export before starting the backend.
	"""
	if not _is_local_request(request):
		return {"error": "forbidden: set_api_key only allowed from loopback (local machine)"}
	key = payload.get("key") or payload.get("OPENAI_API_KEY")
	if not key:
		return {"error": "no key provided; send JSON {\"key\": \"sk-...\"}"}
	os.environ["OPENAI_API_KEY"] = key
	global OPENAI_API_KEY
	OPENAI_API_KEY = key
	return {"status": "ok", "message": "OPENAI_API_KEY set in process. Persist in .env or export to keep across restarts."}

# New: small helper to send email via SMTP (if configured) or persist to outbox
def _send_email(to: str, subject: str, body: str) -> dict:
	"""
	Send email using SMTP env vars if configured, otherwise persist to backend/outbox.
	Env vars supported:
	  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM, SMTP_USE_TLS (true/false), SMTP_USE_SSL (true/false)
	"""
	out = {"sent": False, "method": None, "details": None}
	smtp_host = os.getenv("SMTP_HOST", "")
	if smtp_host:
		try:
			smtp_port = int(os.getenv("SMTP_PORT", "0")) or None
			smtp_user = os.getenv("SMTP_USER", "")
			smtp_pass = os.getenv("SMTP_PASS", "")
			smtp_from = os.getenv("SMTP_FROM", smtp_user or f"no-reply@{os.getenv('HOSTNAME','localhost')}")
			use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() == "true"
			use_tls = os.getenv("SMTP_USE_TLS", "false").lower() == "true"

			msg = EmailMessage()
			msg["From"] = smtp_from
			msg["To"] = to
			msg["Subject"] = subject
			msg.set_content(body)

			if use_ssl:
				server = smtplib.SMTP_SSL(smtp_host, smtp_port or 465, timeout=10)
			else:
				server = smtplib.SMTP(smtp_host, smtp_port or 25, timeout=10)
			try:
				if use_tls and not use_ssl:
					server.starttls()
				if smtp_user and smtp_pass:
					server.login(smtp_user, smtp_pass)
				server.send_message(msg)
			finally:
				server.quit()
			out.update({"sent": True, "method": "smtp", "details": f"sent via {smtp_host}:{smtp_port or ''}"})
			return out
		except Exception as e:
			out.update({"sent": False, "method": "smtp", "details": f"error: {e}"})
			return out

	# Fallback: write to outbox
	try:
		outbox_dir = Path(__file__).resolve().parents[0] / "outbox"
		outbox_dir.mkdir(parents=True, exist_ok=True)
		fn = outbox_dir / f"email_{datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')}.json"
		payload = {"to": to, "subject": subject, "body": body, "created_at": datetime.utcnow().isoformat() + "Z"}
		fn.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
		out.update({"sent": True, "method": "outbox", "details": str(fn)})
		return out
	except Exception as e:
		return {"sent": False, "method": "outbox", "details": f"failed to write outbox: {e}"}

# Add endpoint: /send_email
@app.post("/send_email")
def send_email(payload: dict):
	"""
	Expected JSON: {"to": "...", "subject": "...", "body": "..."
	"""
	to = payload.get("to")
	subject = payload.get("subject")
	body = payload.get("body")
	if not to or not subject or not body:
		return {"error": "missing_fields", "message": "to, subject, and body are required"}
	# Send email via SMTP or persist to outbox
	result = _send_email(to, subject, body)
	return {"status": "ok", "result": result}

# simple user/session persistence (dev-only)
_USERS_FILE = os.path.join(os.path.dirname(__file__), "users.json")
_SESSIONS_FILE = os.path.join(os.path.dirname(__file__), "sessions.json")
_PW_ITER = 100_000
_SESSION_TTL = 24 * 3600  # seconds

def _load_json(path):
		if os.path.exists(path):
			try:
				with open(path, "r", encoding="utf-8") as f:
					return json.load(f)
			except Exception:
				return {}
		return {}

def _save_json(path, obj):
		with open(path, "w", encoding="utf-8") as f:
			json.dump(obj, f, ensure_ascii=False, indent=2)

def _hash_password(password: str, salt: Optional[str] = None):
		# return (salt_hex, hash_hex)
		if salt is None:
			salt_b = os.urandom(16)
			salt = salt_b.hex()
		else:
			salt_b = bytes.fromhex(salt)
		dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_b, _PW_ITER)
		return salt, dk.hex()

def _create_user(name, dob, address, email, password):
		users = _load_json(_USERS_FILE) or {}
		if email in users:
			return None, "email_exists"
		uid = uuid.uuid4().hex
		salt, pw_hash = _hash_password(password)
		users[email] = {"id": uid, "name": name, "dob": dob, "address": address, "email": email, "pw_hash": pw_hash, "salt": salt, "created_at": int(time.time())}
		_save_json(_USERS_FILE, users)
		return users[email], None

def _verify_user(email, password):
		users = _load_json(_USERS_FILE) or {}
		u = users.get(email)
		if not u:
			return None
		salt = u.get("salt")
		_, h = _hash_password(password, salt=salt)
		if h == u.get("pw_hash"):
			return u
		return None

def _create_session(user_id):
		sessions = _load_json(_SESSIONS_FILE) or {}
		token = uuid.uuid4().hex
		sessions[token] = {"user_id": user_id, "created_at": int(time.time()), "expires_at": int(time.time()) + _SESSION_TTL}
		_save_json(_SESSIONS_FILE, sessions)
		return token

def _get_user_by_id(uid):
		users = _load_json(_USERS_FILE) or {}
		for e,u in users.items():
			if u.get("id") == uid:
				return u
		return None

def _get_user_from_token(token: str):
		sessions = _load_json(_SESSIONS_FILE) or {}
		rec = sessions.get(token)
		if not rec:
			return None
		if rec.get("expires_at",0) < int(time.time()):
			# expired -> remove
			del sessions[token]
			_save_json(_SESSIONS_FILE, sessions)
			return None
		return _get_user_by_id(rec["user_id"])

def _delete_session(token: str):
		sessions = _load_json(_SESSIONS_FILE) or {}
		if token in sessions:
			del sessions[token]
			_save_json(_SESSIONS_FILE, sessions)

def _extract_token_from_header(req: Request):
		auth = req.headers.get("authorization") or req.headers.get("Authorization") or ""
		if not auth:
			return None
		parts = auth.split()
		if len(parts) == 2 and parts[0].lower() == "bearer":
			return parts[1]
		return None

# New endpoints: signup / login / me / logout
@app.post("/signup")
def signup(payload: dict):
		# expects: name, dob, address, email, password
		name = payload.get("name")
		dob = payload.get("dob")
		address = payload.get("address")
		email = (payload.get("email") or "").strip().lower()
		password = payload.get("password")
		if not (name and dob and address and email and password):
			return {"error":"missing_fields", "message":"name,dob,address,email,password required"}
		user, err = _create_user(name, dob, address, email, password)
		if err == "email_exists":
			return {"error":"email_exists", "message":"User with this email already exists"}
		# create session token
		token = _create_session(user["id"])
		# return safe profile
		profile = {"id": user["id"], "name": user["name"], "dob": user["dob"], "address": user["address"], "email": user["email"]}
		return {"status":"ok", "token": token, "user": profile}

@app.post("/login")
def login(payload: dict):
		email = (payload.get("email") or "").strip().lower()
		password = payload.get("password")
		if not (email and password):
			return {"error":"missing_fields", "message":"email and password required"}
		user = _verify_user(email, password)
		if not user:
			return {"error":"invalid_credentials", "message":"Invalid email or password"}
		token = _create_session(user["id"])
		profile = {"id": user["id"], "name": user["name"], "dob": user["dob"], "address": user["address"], "email": user["email"]}
		return {"status":"ok", "token": token, "user": profile}

@app.get("/me")
def me(request: Request):
		token = _extract_token_from_header(request)
		if not token:
			return {"error":"unauthenticated"}
		user = _get_user_from_token(token)
		if not user:
			return {"error":"invalid_or_expired_token"}
		return {"status":"ok", "user": {"id": user["id"], "name": user["name"], "dob": user["dob"], "address": user["address"], "email": user["email"]}}

@app.post("/logout")
def logout(request: Request):
		token = _extract_token_from_header(request)
		if not token:
			return {"error":"unauthenticated"}
		_delete_session(token)
		return {"status":"ok", "message":"logged out"}
