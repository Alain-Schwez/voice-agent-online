import httpx
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer
import faiss
import numpy as np
from urllib.parse import urljoin, urlparse
import asyncio
import time

# -----------------------------
# Configuration
# -----------------------------

TARGET_SITE = "https://www.ville-viroflay.fr"   # CHANGE THIS
MAX_PAGES = 40                        # crawl limit
CHUNK_SIZE = 500
REFRESH_INTERVAL = 86400              # 24h


# -----------------------------
# Global objects
# -----------------------------

model = SentenceTransformer("all-MiniLM-L6-v2")

documents = []
index = None
last_refresh = 0


# -----------------------------
# Website crawler
# -----------------------------

async def fetch_page(client, url):

    try:
        r = await client.get(url)
        if r.status_code != 200:
            return None

        soup = BeautifulSoup(r.text, "html.parser")

        text = soup.get_text(separator=" ", strip=True)

        links = []

        for a in soup.find_all("a", href=True):
            link = urljoin(url, a["href"])

            if urlparse(link).netloc == urlparse(TARGET_SITE).netloc:
                links.append(link)

        return text, links

    except Exception:
        return None


async def crawl_site():

    visited = set()
    queue = [TARGET_SITE]

    pages = []

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

            pages.append(text)

            for link in links:
                if link not in visited:
                    queue.append(link)

    return pages


# -----------------------------
# Text chunking
# -----------------------------

def split_chunks(text):

    chunks = []

    for i in range(0, len(text), CHUNK_SIZE):
        chunk = text[i:i + CHUNK_SIZE]

        if len(chunk) > 50:
            chunks.append(chunk)

    return chunks


# -----------------------------
# Build vector index
# -----------------------------

async def build_index():

    global documents
    global index
    global last_refresh

    print("Crawling website...")

    pages = await crawl_site()

    print("Pages crawled:", len(pages))

    documents = []

    for page in pages:
        documents.extend(split_chunks(page))

    print("Total chunks:", len(documents))

    embeddings = model.encode(documents)

    dim = embeddings.shape[1]

    index = faiss.IndexFlatL2(dim)

    index.add(np.array(embeddings))

    last_refresh = time.time()

    print("Vector index built")


# -----------------------------
# Daily refresh
# -----------------------------

async def refresh_loop():

    while True:

        await asyncio.sleep(REFRESH_INTERVAL)

        print("Refreshing website knowledge index...")

        await build_index()


# -----------------------------
# Fast semantic search
# -----------------------------

def search(query, k=5):

    global index

    if index is None:
        return "Knowledge index not ready."

    query_embedding = model.encode([query])

    distances, indices = index.search(query_embedding, k)

    results = []

    for i in indices[0]:

        if i < len(documents):
            results.append(documents[i])

    return "\n\n".join(results)
