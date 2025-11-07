import os, json
from sentence_transformers import SentenceTransformer
import numpy as np
import faiss
from tqdm import tqdm

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
OUT_DIR = os.path.join(os.path.dirname(__file__), "index")
os.makedirs(OUT_DIR, exist_ok=True)

EMBED_MODEL = "all-MiniLM-L6-v2"
CHUNK_SIZE_WORDS = 450
CHUNK_OVERLAP = 80

def read_doc(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def chunk_text(text):
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = words[i:i+CHUNK_SIZE_WORDS]
        chunks.append(" ".join(chunk))
        i += CHUNK_SIZE_WORDS - CHUNK_OVERLAP
    return chunks

def main():
    # Ensure DATA_DIR exists; if not, create it and drop a sample template then exit with instructions.
    if not os.path.isdir(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)
        sample_path = os.path.join(DATA_DIR, "sample_template.txt")
        if not os.path.exists(sample_path):
            sample_content = """source_name: sample_crime
url: https://example.com/sample_crime
date_published: 2025-01-01
jurisdiction: national
doc_type: guide

Title: Sample Crime Guide

Write a short verified "Initial Action Roadmap" here for a specific crime.
Include steps like immediate actions, how to file FIR, evidence to preserve, and likely IPC sections.
"""
            with open(sample_path, "w", encoding="utf-8") as sf:
                sf.write(sample_content)
        print("DATA directory was missing. Created:", DATA_DIR)
        print("A sample file 'sample_template.txt' was added. Please add your .txt/.md source files into this folder and re-run this script.")
        return

    model = None
    try:
        model = SentenceTransformer(EMBED_MODEL)
    except Exception as e:
        print("Failed to load embedding model:", EMBED_MODEL)
        print("Error:", str(e))
        print("Check internet connection and that the model name is correct.")
        return

    docs = []
    for fname in os.listdir(DATA_DIR):
        if not fname.lower().endswith((".txt", ".md")):
            continue
        path = os.path.join(DATA_DIR, fname)
        text = read_doc(path)
        # minimal metadata parsing: expect metadata lines at top
        meta = {"source_name": fname, "url": "", "date_published": "", "jurisdiction": "", "doc_type": ""}
        lines = text.splitlines()
        for ln in lines[:10]:
            if ":" in ln:
                k, v = ln.split(":", 1)
                k = k.strip().lower()
                if k in meta:
                    meta[k] = v.strip()
        chunks = chunk_text(text)
        for i, c in enumerate(chunks):
            docs.append({"id": f"{fname}__chunk{i}", "text": c, "meta": meta})

    if len(docs) == 0:
        print("No .txt or .md files found in", DATA_DIR)
        print("Please add source documents (one per file). Each file should include small metadata lines at the top like:")
        print("  source_name: ...")
        print("  url: ...")
        print("  date_published: ...")
        print("  jurisdiction: ...")
        print("  doc_type: ...")
        print("A sample 'sample_template.txt' has been placed in the folder if you need an example.")
        return

    texts = [d["text"] for d in docs]
    try:
        embeds = model.encode(texts, show_progress_bar=True, convert_to_numpy=True)
    except Exception as e:
        print("Failed to create embeddings. Error:", str(e))
        return

    dim = embeds.shape[1]
    try:
        index = faiss.IndexFlatIP(dim)
        faiss.normalize_L2(embeds)
        index.add(embeds)
        faiss.write_index(index, os.path.join(OUT_DIR, "faiss_index.faiss"))
    except Exception as e:
        print("Failed to build or write FAISS index. Error:", str(e))
        return

    # save metadata
    with open(os.path.join(OUT_DIR, "metadata.jsonl"), "w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print("Index built:", len(docs))

if __name__ == "__main__":
    main()
