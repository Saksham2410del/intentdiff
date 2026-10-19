import numpy as np
import yaml
from chromadb.api.types import EmbeddingFunction

from intentdiff import config
from intentdiff.history import PastTicket, TicketHistory, rrf
from intentdiff.policy import check_sql

PAST = [
    PastTicket("H1", "Close account 2244, customer request, effective 1998-11-30.",
               "UPDATE account SET status = 'closed', closed_on = '1998-11-30' WHERE account_id = 2244;"),
    PastTicket("H2", "Client 509 has a new phone number: +420 602 118 334",
               "UPDATE client SET phone = '+420 602 118 334' WHERE client_id = 509;"),
    PastTicket("H3", "Delete the loan_payment standing order on account 97.",
               "DELETE FROM standing_order WHERE account_id = 97 AND purpose = 'loan_payment';"),
]


class WordCounts(EmbeddingFunction):
    """A tiny stand-in for a real embedding model, so tests don't download anything."""

    VOCAB = ["account", "close", "closed", "phone", "number", "standing", "order", "delete", "shut", "down"]

    def __init__(self):
        pass

    def __call__(self, input):
        vectors = []
        for text in input:
            words = text.lower().replace(",", " ").replace(".", " ").split()
            # "shut down" counts as "close", which is the kind of thing embeddings get and BM25 doesn't
            words = ["close" if w in ("shut", "down") else w for w in words]
            vectors.append(np.array([words.count(v) + 0.01 for v in self.VOCAB], dtype=float))
        return vectors

    @staticmethod
    def name():
        return "word-counts"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return WordCounts()


def test_rrf_prefers_items_that_rank_well_in_both_lists():
    assert rrf([["a", "b"], ["b", "c"]]) == ["b", "a", "c"]


def test_keyword_search_matches_exact_words():
    history = TicketHistory(PAST, vectors=False)
    assert history.mode == "keyword only"
    assert history.search("please delete a standing order", k=1)[0].id == "H3"


def test_vector_search_finds_meaning_without_shared_words():
    history = TicketHistory(PAST, embedding_function=WordCounts())
    assert history.mode == "hybrid"
    # "shut down" shares no word with "Close account ...", only the meaning
    assert history.vector_search("shut down 4071", 1) == ["H1"]
    assert history.keyword_search("shut down 4071", 1) == []
    assert history.search("shut down 4071", k=1)[0].id == "H1"


def test_history_file_is_valid(schema):
    history = TicketHistory.load(vectors=False)
    assert len(history.ids) >= 20
    for ticket in history.tickets.values():
        assert check_sql(ticket.fix, schema).ok, ticket.id


def test_history_does_not_copy_the_benchmark():
    benchmark = yaml.safe_load((config.ROOT / "eval" / "tickets.yaml").read_text(encoding="utf-8"))
    benchmark_sql = set()
    for t in benchmark:
        for sql in [t.get("gold"), t.get("script"), *t.get("rewrites", []), *t.get("bad", [])]:
            if sql:
                benchmark_sql.add(" ".join(sql.split()))
    benchmark_tickets = {" ".join(t["ticket"].split()) for t in benchmark}

    for past in TicketHistory.load(vectors=False).tickets.values():
        assert " ".join(past.fix.split()) not in benchmark_sql, past.id
        assert " ".join(past.ticket.split()) not in benchmark_tickets, past.id
