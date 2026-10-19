"""Past tickets and the fixes that were approved for them - the retrieval (RAG) part.

When a new ticket comes in we look up the most similar old tickets and show
the model how they were fixed. That teaches it this team's habits: closing an
account also sets closed_on, "remove the phone" means NULL and not '', date
ranges in tickets include both ends, and so on.

Search is hybrid, because each half misses things the other one finds:
  - BM25 (keywords) is good at exact words: status names, column names, IDs
  - embeddings (Chroma) are good at meaning: "shut down" is close to "close"
The two rankings are merged with reciprocal rank fusion (RRF).
"""
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml
from rank_bm25 import BM25Okapi

from intentdiff import config

log = logging.getLogger(__name__)

# 60 is the constant from the original RRF paper (Cormack et al., 2009). It
# stops the top spot of one list from drowning out everything else.
RRF_K = 60


@dataclass
class PastTicket:
    id: str
    ticket: str
    fix: str


def tokenize(text):
    # keeps running_in_debt and standing_order in one piece
    return re.findall(r"[a-z0-9_]+", text.lower())


def rrf(rankings, k=RRF_K):
    """Merge ranked lists of ids. Each list gives an id 1 / (k + its rank)."""
    scores = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0) + 1 / (k + rank)
    return sorted(scores, key=lambda item: scores[item], reverse=True)


class TicketHistory:
    def __init__(self, tickets, vectors=True, embedding_function=None):
        self.tickets = {t.id: t for t in tickets}
        self.ids = list(self.tickets)
        self.bm25 = BM25Okapi([tokenize(t.ticket) for t in tickets])
        self.collection = None
        if vectors:
            self._build_vector_index(embedding_function)

    @classmethod
    def load(cls, path=config.HISTORY_PATH, **kwargs):
        rows = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        tickets = [PastTicket(r["id"], r["ticket"].strip(), r["fix"].strip()) for r in rows]
        return cls(tickets, **kwargs)

    @property
    def mode(self):
        return "hybrid" if self.collection is not None else "keyword only"

    def _build_vector_index(self, embedding_function):
        try:
            import chromadb
            # In-memory is plenty for a few hundred tickets. The name is random
            # because Chroma shares collections between clients in one process.
            client = chromadb.EphemeralClient()
            options = {"embedding_function": embedding_function} if embedding_function else {}
            collection = client.create_collection(f"tickets-{uuid.uuid4().hex[:8]}", **options)
            # Chroma's default model (all-MiniLM-L6-v2) is downloaded on first use, about 80 MB
            collection.add(ids=self.ids, documents=[self.tickets[i].ticket for i in self.ids])
            self.collection = collection
        except Exception as e:
            # Retrieval only adds examples to the prompt, it never decides anything,
            # so keyword search alone is an acceptable fallback.
            log.warning("vector search is off, using keyword search only: %s", e)

    def keyword_search(self, text, n):
        scores = self.bm25.get_scores(tokenize(text))
        ranked = sorted(range(len(self.ids)), key=lambda i: scores[i], reverse=True)
        return [self.ids[i] for i in ranked[:n] if scores[i] > 0]

    def vector_search(self, text, n):
        if self.collection is None:
            return []
        found = self.collection.query(query_texts=[text], n_results=min(n, len(self.ids)))
        return found["ids"][0]

    def search(self, text, k=config.EXAMPLES_IN_PROMPT, pool=10):
        ranked = rrf([self.keyword_search(text, pool), self.vector_search(text, pool)])
        return [self.tickets[i] for i in ranked[:k]]
