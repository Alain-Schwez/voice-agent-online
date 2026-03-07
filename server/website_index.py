import httpx
from bs4 import BeautifulSoup
import faiss
import numpy as np
from urllib.parse import urljoin, urlparse
import asyncio
import time
import xml.etree.ElementTree as ET
import re
import hashlib
import pickle
import os
import gc

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------

TARGET_SITE = "https://www.ville-viroflay.fr"
MAX_PAGES = 40
CHUNK_SIZE = 200
REFRESH_INTERVAL = 86400

INDEX_FILE = "vector_index.faiss"
DOC_FILE = "documents.pkl"
HASH_FILE = "page_hashes.pkl"

# OpenAI embedding settings
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_EMBED_MODEL = "text-embedding-3-small"
EMBED_BATCH = 32
OPENAI_API_URL = "https://api.openai.com/v1/embeddings"

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY environment variable is required for embeddings")

# ------------------------------------------------------------------------------
# Globals
# ------------------------------------------------------------------------------

documents = []
page_hashes = {}
index = None


# ------------------------------------------------------------------------------
# Utility
# ------------------------------------------------------------------------------

def hash_text(text):
    return hashlib.md5(text.encode()).hexdigest()


# ------------------------------------------------------------------------------
# Fetch page
# ------------------------------------------------------------------------------

async def fetch_page(client, url):
    try:
        r = await client.get(url)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        for tag in soup.find_all(["nav", "footer", "header", "aside"]):
            tag.decompose()
        for tag in soup.select("[class*='cookie'], [id*='cookie']"):
            tag.decompose()
        main_content = None
        for selector in ["main", "article", "[role=main]", ".content", ".main", "#content"]:
            main_content = soup.select_one(selector)
            if main_content:
                break
        text = main_content.get_text(separator=" ", strip=True) if main_content else soup.get_text(separator=" ", strip=True)
        text = " ".join(text.split())
        links = []
        for a in soup.find_all("a", href=True):
            link = urljoin(url, a["href"])
            parsed = urlparse(link)
            if parsed.netloc != urlparse(TARGET_SITE).netloc:
                continue
            if parsed.query:
                continue
            if link.endswith((".jpg",".jpeg",".png",".gif",".svg",".zip",".pdf")):
                continue
            if any(x in link.lower() for x in ["login","signup","register","cart","checkout","account","privacy","terms"]):
                continue
            links.append(link)
        return text, links
    except Exception:
        return None


# ------------------------------------------------------------------------------
# Sitemap reader
# ------------------------------------------------------------------------------

async def get_sitemap_urls():
    sitemap_url = TARGET_SITE.rstrip("/") + "/sitemap.xml"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(sitemap_url)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
        urls = []
        for loc in root.iter("{*}loc"):
            urls.append(loc.text.strip())
        print("Sitemap URLs found:", len(urls))
        return urls
    except Exception:
        return []


# ------------------------------------------------------------------------------
# Crawl site
# ------------------------------------------------------------------------------

async def crawl_site():
    visited = set()
    queue = await get_sitemap_urls()
    if not queue:
        queue = [TARGET_SITE]
    pages = {}
    async with httpx.AsyncClient(timeout=20) as client:
        while queue and len(visited) < MAX_PAGES:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            result = await fetch_page(client, url)
            if not result:
                continue
            text, links = result
            pages[url] = text
            for link in links:
                if link not in visited:
                    queue.append(link)
    return pages


# ------------------------------------------------------------------------------
# Text chunking
# ------------------------------------------------------------------------------

def split_chunks(text):
    chunks = []
    for i in range(0, len(text), CHUNK_SIZE):
        chunk = text[i:i + CHUNK_SIZE]
        if len(chunk) > 50:
            chunks.append(chunk)
    return chunks


# ------------------------------------------------------------------------------
# Persistent index loader
# ------------------------------------------------------------------------------

def load_index():
    global index, documents, page_hashes
    if os.path.exists(INDEX_FILE):
        print("Loading saved vector index...")
        index = faiss.read_index(INDEX_FILE)
        with open(DOC_FILE, "rb") as f:
            documents = pickle.load(f)
        if os.path.exists(HASH_FILE):
            with open(HASH_FILE, "rb") as f:
                page_hashes = pickle.load(f)
        print("Index loaded. Chunks:", len(documents))
        return True
    return False


# ------------------------------------------------------------------------------
# Save index
# ------------------------------------------------------------------------------

def save_index():
    faiss.write_index(index, INDEX_FILE)
    with open(DOC_FILE, "wb") as f:
        pickle.dump(documents, f)
    with open(HASH_FILE, "wb") as f:
        pickle.dump(page_hashes, f)


# ------------------------------------------------------------------------------
# OpenAI Embeddings (sync helper, batched)
# ------------------------------------------------------------------------------

def get_embeddings_sync(texts: list[str]) -> np.ndarray:
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    all_embs = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i : i + EMBED_BATCH]
        payload = {"model": OPENAI_EMBED_MODEL, "input": batch}
        resp = httpx.post(OPENAI_API_URL, json=payload, headers=headers, timeout=30.0)
        resp.raise_for_status()
        j = resp.json()
        for item in j["data"]:
            all_embs.append(item["embedding"])
    return np.array(all_embs, dtype=np.float32)


# ------------------------------------------------------------------------------
# Build / update index (stream embeddings to FAISS to reduce peak memory)
# ------------------------------------------------------------------------------

async def build_index():
    global documents, index, page_hashes

    pages = await crawl_site()
    print("Pages crawled:", len(pages))

    new_chunks = []
    changed = False

    for url, text in pages.items():
        h = hash_text(text)
        if url in page_hashes and page_hashes[url] == h:
            continue
        changed = True
        page_hashes[url] = h
        chunks = split_chunks(text)
        new_chunks.extend(chunks)

    if not changed and index is not None:
        print("No page changes detected")
        return

    print("Updating vector index...")
    # Append documents list and stream embeddings in batches to avoid large arrays
    start_idx = len(documents)
    documents.extend(new_chunks)

    # if no new chunks, nothing to do
    if not new_chunks:
        print("No new chunks to embed")
        return

    # Ensure index exists or create it from first batch's dim
    first_batch = new_chunks[:EMBED_BATCH]
    emb_first = get_embeddings_sync(first_batch)
    if index is None:
        dim = emb_first.shape[1]
        index = faiss.IndexFlatL2(dim)
    index.add(np.array(emb_first))
    # free memory for first batch
    del emb_first
    gc.collect()

    # process remaining batches
    for i in range(EMBED_BATCH, len(new_chunks), EMBED_BATCH):
        batch = new_chunks[i : i + EMBED_BATCH]
        emb = get_embeddings_sync(batch)
        index.add(np.array(emb))
        del emb
        gc.collect()

    save_index()
    print("Index updated. Total chunks:", len(documents))


# ------------------------------------------------------------------------------
# Refresh loop
# ------------------------------------------------------------------------------

async def refresh_loop():
    while True:
        await asyncio.sleep(REFRESH_INTERVAL)
        print("Refreshing knowledge index...")
        await build_index()


# ------------------------------------------------------------------------------
# Context compression before sending text to LLM
# ------------------------------------------------------------------------------

def compress_context(query: str, chunks: list[str], max_sentences: int = 6) -> str:
    sentences = []
    for chunk in chunks:
        for s in re.split(r"(?<=[\.\!\?])\s+", chunk.strip()):
            s = s.strip()
            if len(s) >= 30:
                sentences.append(s)
    if not sentences:
        return "\n\n".join(chunks[:2])
    q_emb = get_embeddings_sync([query]).astype(np.float32)
    s_emb = get_embeddings_sync(sentences).astype(np.float32)
    def normalize(a):
        norms = np.linalg.norm(a, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return a / norms
    q_emb = normalize(q_emb)
    s_emb = normalize(s_emb)
    sims = (s_emb @ q_emb[0]).tolist()
    q_words = set(re.findall(r"\w+", query.lower()))
    scored = []
    for sent, sim in zip(sentences, sims):
        s_words = set(re.findall(r"\w+", sent.lower()))
        kw = len(q_words & s_words)
        score = (0.9 * float(sim)) + (0.1 * (kw / max(1, len(q_words))))
        scored.append((score, sent))
    scored.sort(reverse=True, key=lambda x: x[0])
    top = [sent for _, sent in scored[:max_sentences]]
    dedup = []
    seen = set()
    for s in top:
        key = s.lower()
        if key not in seen:
            seen.add(key)
            dedup.append(s)
    return "\n".join(dedup)


# ------------------------------------------------------------------------------
# Hybrid search
# ------------------------------------------------------------------------------

def search(query, k=5):
    global index
    if index is None:
        return "Knowledge index not ready."
    query_embedding = get_embeddings_sync([query]).astype(np.float32)
    qe = query_embedding if query_embedding.ndim == 2 else query_embedding.reshape(1, -1)
    distances, indices = index.search(qe, k * 3)
    semantic_results = []
    for i in indices[0]:
        if i < len(documents):
            semantic_results.append(documents[i])
    query_words = set(re.findall(r"\w+", query.lower()))
    scored = []
    for chunk in semantic_results:
        chunk_words = set(re.findall(r"\w+", chunk.lower()))
        keyword_score = len(query_words & chunk_words)
        scored.append((keyword_score, chunk))
    scored.sort(reverse=True, key=lambda x: x[0])
    results = [chunk for score, chunk in scored[:k]]
    return "\n\n".join(results)
