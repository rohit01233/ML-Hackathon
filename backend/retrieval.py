import os
import json
import re
import logging
from pathlib import Path
from typing import List, Dict, Optional

import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

# Configuration
EMBED_MODEL = "all-MiniLM-L6-v2"
KB_FILENAME = "nyay_sahayak_templates_from_csv.md"  # located in project root
INDEX_DIR = os.path.join(os.path.dirname(__file__), "index")
os.makedirs(INDEX_DIR, exist_ok=True)

# Module state
_INDEX = None  # faiss index
_PASSAGES: List[Dict] = []  # list of {"id": int, "text": str, "meta": {...}}
_PASSAGE_EMBS = None
_MODEL: Optional[SentenceTransformer] = None

logger = logging.getLogger("backend.retrieval")
logger.setLevel(logging.INFO)


def _find_kb_path() -> Path:
    # project root assumed one level up from backend/
    root = Path(__file__).resolve().parents[1]
    candidate = root / KB_FILENAME
    if not candidate.exists():
        candidate = Path(os.getcwd()) / KB_FILENAME
    return candidate


def _chunk_markdown(md_text: str) -> List[Dict]:
    """
    Split the markdown by top-level '## ' sections. Each section becomes one passage.
    If no '##' headings found, treat whole document as single passage.
    """
    parts = re.split(r"(?m)^\s*##\s+", md_text)
    passages = []
    cur_id = 0
    if len(parts) == 1:
        text = parts[0].strip()
        if text:
            passages.append({"id": cur_id, "text": text, "meta": {"source_name": KB_FILENAME, "section_title": None, "jurisdiction": None}})
        return passages

    for p in parts:
        p = p.strip()
        if not p:
            continue
        lines = p.splitlines()
        title = lines[0].strip() if lines else f"section_{cur_id}"
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        text = (title + "\n\n" + body).strip() if body else title
        meta = {"source_name": KB_FILENAME, "section_title": title, "jurisdiction": None}
        passages.append({"id": cur_id, "text": text, "meta": meta})
        cur_id += 1
    return passages


def load_resources(force_reload: bool = False):
    """
    Load knowledge base, compute embeddings and build FAISS index.
    If a persisted index + metadata exists in backend/index, load that to speed startup.
    """
    global _INDEX, _PASSAGES, _PASSAGE_EMBS, _MODEL

    if _INDEX is not None and not force_reload:
        logger.info("Resources already loaded; skipping load.")
        return

    idx_path = os.path.join(INDEX_DIR, "faiss_index.faiss")
    meta_path = os.path.join(INDEX_DIR, "metadata.jsonl")

    # Try to load persisted index + metadata
    if os.path.exists(idx_path) and os.path.exists(meta_path) and not force_reload:
        try:
            logger.info("Loading persisted FAISS index from %s", idx_path)
            _INDEX = faiss.read_index(idx_path)
            _PASSAGES = []
            with open(meta_path, "r", encoding="utf-8") as f:
                for line in f:
                    _PASSAGES.append(json.loads(line))
            # lazy-load model (for query-time encoding)
            if _MODEL is None:
                _MODEL = SentenceTransformer(EMBED_MODEL)
            logger.info("Loaded index with %d passages", len(_PASSAGES))
            return
        except Exception as e:
            logger.warning("Failed to load persisted index/metadata (%s): will rebuild. Error: %s", idx_path, e)

    # Build from KB markdown
    kb_path = _find_kb_path()
    if not kb_path.exists():
        raise FileNotFoundError(f"Knowledge base file not found at {kb_path}")

    logger.info("Building index from KB: %s", kb_path)
    text = kb_path.read_text(encoding="utf-8")
    _PASSAGES = _chunk_markdown(text)
    texts = [p["text"] for p in _PASSAGES]
    if not texts:
        raise ValueError("No passages extracted from knowledge base.")

    logger.info("Loading embedding model: %s", EMBED_MODEL)
    _MODEL = SentenceTransformer(EMBED_MODEL)
    logger.info("Computing embeddings for %d passages", len(texts))
    embs = _MODEL.encode(texts, convert_to_numpy=True, show_progress_bar=True)
    embs = embs.astype("float32")
    faiss.normalize_L2(embs)
    dim = embs.shape[1]
    logger.info("Building FAISS index (IndexFlatIP) dim=%d", dim)
    _INDEX = faiss.IndexFlatIP(dim)
    _INDEX.add(embs)
    _PASSAGE_EMBS = embs

    # persist index and metadata for faster restart
    try:
        faiss.write_index(_INDEX, idx_path)
        with open(meta_path, "w", encoding="utf-8") as f:
            for p in _PASSAGES:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
        logger.info("Persisted index and metadata to %s", INDEX_DIR)
    except Exception as e:
        logger.warning("Failed to persist index/metadata: %s", e)


def retrieve(query: str, top_k: int = 50, rerank_top: int = 6, jurisdiction: Optional[str] = None) -> List[Dict]:
    """
    Return top_k passages matching the query as a list of dicts:
      {"id": int, "text": str, "meta": {...}, "score": float}
    """
    global _INDEX, _MODEL, _PASSAGES

    if _INDEX is None or _MODEL is None or not _PASSAGES:
        logger.info("Index/model not loaded; calling load_resources()")
        load_resources()

    q_emb = _MODEL.encode([query], convert_to_numpy=True)
    q_emb = q_emb.astype("float32")
    faiss.normalize_L2(q_emb)

    D, I = _INDEX.search(q_emb, top_k)
    D = D[0]
    I = I[0]

    hits = []
    for score, idx in zip(D, I):
        idx = int(idx)
        if idx < 0 or idx >= len(_PASSAGES):
            continue
        doc = _PASSAGES[idx]
        # jurisdiction filtering if metadata present
        if jurisdiction:
            meta_j = doc.get("meta", {}).get("jurisdiction")
            if meta_j and meta_j != jurisdiction:
                continue
        hits.append({"id": doc["id"], "text": doc["text"], "meta": doc["meta"], "score": float(score)})

    return hits


if __name__ == "__main__":
    print("Nyay Sahayak — retrieval helper")
    print("Run steps (project root):")
    print("  1) Create venv and install deps: python -m venv .venv && .venv\\Scripts\\activate && pip install -r requirements.txt")
    print("  2) Add your source markdown file:", KB_FILENAME)
    print("  3) Build index: python backend/preprocess.py")
    print("  4) Start backend: uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000")
    print("")
    print("Troubleshooting:")
    print("  - If faiss import fails, install faiss-cpu or follow platform-specific faiss install instructions.")
    print("  - If embeddings model download fails, ensure internet access and enough disk space.")
