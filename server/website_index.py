import httpx
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer
import faiss   # --- vector search engine optimized for similarity search, developed by Meta Platforms
import numpy as np
from urllib.parse import urljoin, urlparse
import asyncio
import time
import xml.etree.ElementTree as ET
import re
import hashlib
import pickle
import os

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------

TARGET_SITE = "https://www.ville-viroflay.fr"
MAX_PAGES = 40
CHUNK_SIZE = 500
REFRESH_INTERVAL = 86400

INDEX_FILE = "vector_index.faiss"
DOC_FILE = "documents.pkl"
HASH_FILE = "page_hashes.pkl"

# ------------------------------------------------------------------------------
# Globals
# ------------------------------------------------------------------------------

model = SentenceTransformer("all-MiniLM-L6-v2")

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

        # remove noise
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        for tag in soup.find_all(["nav", "footer", "header", "aside"]):
            tag.decompose()

        for tag in soup.select("[class*='cookie'], [id*='cookie']"):
            tag.decompose()

        # main content extraction
        main_content = None

        for selector in ["main", "article", "[role=main]", ".content", ".main", "#content"]:
            main_content = soup.select_one(selector)
            if main_content:
                break

        if main_content:
            text = main_content.get_text(separator=" ", strip=True)
        else:
            text = soup.get_text(separator=" ", strip=True)

        text = " ".join(text.split())

        # collect links
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

            if any(x in link.lower() for x in [
                "login","signup","register","cart","checkout",
                "account","privacy","terms"
            ]):
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

        with open(DOC_FILE,"rb") as f:
            documents = pickle.load(f)

        if os.path.exists(HASH_FILE):
            with open(HASH_FILE,"rb") as f:
                page_hashes = pickle.load(f)

        print("Index loaded. Chunks:", len(documents))

        return True

    return False


# ------------------------------------------------------------------------------
# Save index
# ------------------------------------------------------------------------------

def save_index():

    faiss.write_index(index, INDEX_FILE)

    with open(DOC_FILE,"wb") as f:
        pickle.dump(documents,f)

    with open(HASH_FILE,"wb") as f:
        pickle.dump(page_hashes,f)


# ------------------------------------------------------------------------------
# Build / update index
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

    documents.extend(new_chunks)

    embeddings = model.encode(new_chunks)

    if index is None:

        dim = embeddings.shape[1]
        index = faiss.IndexFlatL2(dim)

    index.add(np.array(embeddings))

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
#                 Context compression befor sending text to LLM
# ------------------------------------------------------------------------------

def compress_context(query: str, chunks: list[str], max_sentences: int = 6) -> str:
    """
    Reduce retrieved chunks to only the most relevant sentences.
    Combines sentence semantic similarity + keyword overlap.
    """

    # Split chunks into sentences (simple heuristic) ---------------------------
    sentences = []
    for chunk in chunks:
        for s in re.split(r"(?<=[\.\!\?])\s+", chunk.strip()):
            s = s.strip()
            if len(s) >= 30:
                sentences.append(s)

    if not sentences:
        return "\n\n".join(chunks[:2])

    # Semantic similarity: embed query once, embed sentences once --------------
    q_emb = model.encode([query], normalize_embeddings=True)
    s_emb = model.encode(sentences, normalize_embeddings=True)

    # cosine similarity since normalized: dot product --------------------------
    sims = (s_emb @ q_emb[0]).tolist()

    # Keyword overlap ----------------------------------------------------------
    q_words = set(re.findall(r"\w+", query.lower()))
    scored = []

    for sent, sim in zip(sentences, sims):
        s_words = set(re.findall(r"\w+", sent.lower()))
        kw = len(q_words & s_words)

        # Weighted score: adjust weights if you want ---------------------------
        # "sim" is the cosine similarity between the user query embedding and the
          sentence embedding
          " kw / max(1, len(q_words)": q_words = number of words in the query, kw = number of shared words
        score = (0.9 * float(sim)) + (0.1 * (kw / max(1, len(q_words))))
        scored.append((score, sent))

    scored.sort(reverse=True, key=lambda x: x[0])

    top = [sent for _, sent in scored[:max_sentences]]

    # Deduplicate near-identical sentences -------------------------------------
    dedup = []
    seen = set()
    for s in top:
        key = s.lower()
        if key not in seen:
            seen.add(key)
            dedup.append(s)

    return "\n".join(dedup)

# ------------------------------------------------------------------------------
#                                  Hybrid search
# ------------------------------------------------------------------------------

def search(query, k=5):

    global index

    if index is None:
        return "Knowledge index not ready."

    query_embedding = model.encode([query])

    distances, indices = index.search(query_embedding, k * 3)

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

