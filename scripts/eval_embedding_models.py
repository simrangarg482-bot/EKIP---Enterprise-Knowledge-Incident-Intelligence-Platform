"""Embedding-model comparison harness for EKIP's retrieval layer.

WHAT THIS IS
    Compares retrieval quality between the current production embedding
    model (ENGINEERING_DECISIONS.md #006: `sentence-transformers/
    all-MiniLM-L6-v2`, 384 dimensions) and a stronger candidate (`BAAI/
    bge-base-en-v1.5`, 768 dimensions -- one of the two alternatives #006's
    own "Alternatives considered" section named as "revisitable once real
    query/retrieval data exists to benchmark against"), using the same
    golden question set `scripts/eval_confidence.py` uses
    (`eval_confidence_dataset.json`) against the real, already-ingested
    `test-org` corpus.

    Two metrics, per model:
      - **retrieval recall@5** -- of the questions whose dataset entry
        names real `evidence` (verbatim corpus quotes -- every
        `clear-answer` and `ambiguous` question; `no-information` questions
        have none, since nothing in the corpus is relevant to them), the
        fraction where at least one `evidence` string appears in the top-5
        chunks this model's embedding ranks highest for that question.
      - **answer-grounding rate** -- of the `clear-answer` questions (the
        ones that genuinely should be answerable), the fraction where
        feeding those same top-5 chunks through the REAL production
        answer-generation and grounding-verification code
        (`agents.answer.generation.generate_answer`,
        `agents.answer.grounding.verify_grounding`) produces an answer with
        at least one sentence that survives grounding. Also reported for
        `ambiguous`/`no-information` as a secondary "false-confidence rate"
        check -- correct behavior there is a LOW grounded rate (declining),
        so a model that scores high there is producing confidently
        ungrounded answers, not "better" ones.

HOW THIS AVOIDS TOUCHING PRODUCTION OR THE DATABASE
    Neither model is wired into the app. `<collection>_chunks.embedding` is
    a fixed-width pgvector column (`VECTOR(384)`, ENGINEERING_DECISIONS.md
    #006) -- there is nowhere to even store a 768-dim candidate vector
    without a schema change, which is exactly the migration this script
    exists to gather evidence for before anyone commits to it. Instead:
    every chunk's real `content` for `test-org`'s three collections is read
    directly (bypassing `retrieval.service.search`, which is bound to the
    stored 384-dim column and RRF/lexical fusion this comparison
    deliberately isolates embedding quality from), embedded in-memory with
    each model, and ranked by cosine similarity in plain Python -- a
    read-only, parallel retrieval path used only for this comparison. The
    downstream answer-generation and grounding-verification steps ARE the
    real production functions, run against whichever chunks each model's
    in-memory ranking selected -- so the grounding-rate number reflects
    what the real pipeline would actually do with that retrieval, not a
    re-implementation of it.

WHY IT IS A SCRIPT, NOT A pytest TEST
    Same reasoning as `tests/rag_validation/run_validation.py` and
    `scripts/eval_confidence.py`: needs a live database with real ingested
    data, downloads a ~440MB model on first run, and makes real (if
    comparatively few -- no retries, no Investigation Agent) `OPENAI_API_KEY`
    calls for answer generation/grounding.

RUN
    python scripts/eval_embedding_models.py
    python scripts/eval_embedding_models.py --category clear-answer
    python scripts/eval_embedding_models.py --top-k 5 --limit 5

SIDE EFFECTS
    None on the database -- `generate_answer`/`verify_grounding` are called
    directly, not through `agents.service.answer_question`, so no
    `agent_executions` row is written (unlike `eval_confidence.py`/
    `run_validation.py`'s production-entry-point calls).

IF THE CANDIDATE WINS MEANINGFULLY
    Per this script's own instructions, do not switch `retrieval/
    embedding.py`'s model off the back of this report alone -- write a new
    numbered entry in `docs/ENGINEERING_DECISIONS.md` covering the
    re-embedding strategy and cost/latency tradeoff first (schema migration
    for every `<collection>_chunks.embedding` column, re-embedding every
    existing chunk, ingestion-pipeline cost/latency impact), and let that
    decision -- not this script -- be what authorizes the production change.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Awaitable, Callable

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _silence(*logger_names: str) -> None:
    for name in logger_names:
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        logger.disabled = True


_silence("sqlalchemy.engine", "sqlalchemy.engine.Engine", "sqlalchemy.pool", "httpx", "openai")

from sentence_transformers import SentenceTransformer  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.agents.answer.generation import generate_answer, is_no_answer  # noqa: E402
from app.agents.answer.grounding import split_sentences, verify_grounding  # noqa: E402
from app.agents.llm import get_llm  # noqa: E402
from app.agents.retrieval.context_assembly import assemble_context  # noqa: E402
from app.database.models.ingestion_models import Document  # noqa: E402
from app.database.models.retrieval_models import CodeChunk, ConversationChunk, DocumentationChunk  # noqa: E402
from app.database.models.tenancy_models import Organization  # noqa: E402
from app.database.session import session_scope, set_tenant_context  # noqa: E402
from app.retrieval import embedding as production_embedding  # noqa: E402
from app.retrieval.schemas import ScoredChunk  # noqa: E402

_DATASET_PATH = Path(__file__).resolve().parent / "eval_confidence_dataset.json"
_DEFAULT_ORG_SLUG = "test-org"
_DEFAULT_REPORT_PATH = Path(__file__).resolve().parent / "eval_embedding_models_report.json"
_DEFAULT_TOP_K = 5

# What "wins meaningfully" means for this script's own recommendation: below
# this margin on both headline metrics, a single question's worth of noise
# (1/14 clear-answer questions is already a 0.071 swing) could explain the
# whole difference -- not something to base a re-embedding migration on.
_MEANINGFUL_IMPROVEMENT_MARGIN = 0.10

_CANDIDATE_MODEL_NAME = "BAAI/bge-base-en-v1.5"
_CANDIDATE_DIMENSION = 768
# BAAI's own model card recommends prefixing *queries* (not passages) with
# this instruction for retrieval tasks; passages/documents are embedded as
# plain text. Omitting this would understate the model's real retrieval
# quality, not fairly represent it.
_BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


# --------------------------------------------------------------------------
# model adapters
# --------------------------------------------------------------------------


@lru_cache
def _get_candidate_model() -> SentenceTransformer:
    return SentenceTransformer(_CANDIDATE_MODEL_NAME)


async def _candidate_embed_query(query: str) -> list[float]:
    model = _get_candidate_model()
    vector = await asyncio.to_thread(
        model.encode, _BGE_QUERY_INSTRUCTION + query, normalize_embeddings=True
    )
    return vector.tolist()


async def _candidate_embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = _get_candidate_model()
    vectors = await asyncio.to_thread(model.encode, texts, normalize_embeddings=True)
    return vectors.tolist()


@dataclass(frozen=True)
class ModelAdapter:
    key: str
    label: str
    dimension: int
    embed_query: Callable[[str], Awaitable[list[float]]]
    embed_texts: Callable[[list[str]], Awaitable[list[list[float]]]]


_MODELS = [
    ModelAdapter(
        key="current",
        label="current: sentence-transformers/all-MiniLM-L6-v2 (384-dim, ENGINEERING_DECISIONS.md #006)",
        dimension=production_embedding.EMBEDDING_DIMENSION,
        embed_query=production_embedding.embed_query,
        embed_texts=production_embedding.embed_texts,
    ),
    ModelAdapter(
        key="candidate",
        label=f"candidate: {_CANDIDATE_MODEL_NAME} ({_CANDIDATE_DIMENSION}-dim)",
        dimension=_CANDIDATE_DIMENSION,
        embed_query=_candidate_embed_query,
        embed_texts=_candidate_embed_texts,
    ),
]


# --------------------------------------------------------------------------
# corpus
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ChunkRow:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    collection: str
    content: str
    source_offset_start: int
    source_offset_end: int
    title: str | None
    source_url: str | None


async def _resolve_org(slug: str) -> tuple[uuid.UUID, str, int]:
    async with session_scope() as session:
        row = (
            await session.execute(select(Organization).where(Organization.slug == slug))
        ).scalar_one_or_none()
        if row is None:
            raise SystemExit(f"No organization with slug {slug!r} exists in this database.")
        await set_tenant_context(session, row.id)
        doc_count = len(
            (
                await session.execute(
                    select(Document.id).where(
                        Document.organization_id == row.id, Document.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        return row.id, row.name, doc_count


async def _fetch_corpus(organization_id: uuid.UUID) -> list[ChunkRow]:
    """Every chunk across all three collections for `organization_id` --
    only 93 rows for `test-org` at the time this script was written, small
    enough to embed and rank entirely in memory with no pagination.
    """
    rows: list[ChunkRow] = []
    async with session_scope() as session:
        await set_tenant_context(session, organization_id)
        for collection_name, model in (
            ("documentation", DocumentationChunk),
            ("code", CodeChunk),
            ("conversations", ConversationChunk),
        ):
            result = await session.execute(
                select(
                    model.id,
                    model.document_id,
                    model.content,
                    model.source_offset_start,
                    model.source_offset_end,
                    Document.title,
                    Document.source_url,
                )
                .join(Document, Document.id == model.document_id)
                .where(model.organization_id == organization_id)
            )
            for chunk_id, document_id, content, start, end, title, source_url in result.all():
                rows.append(
                    ChunkRow(chunk_id, document_id, collection_name, content, start, end, title, source_url)
                )
    return rows


# --------------------------------------------------------------------------
# per-model retrieval + grounding
# --------------------------------------------------------------------------


def _dot(a: list[float], b: list[float]) -> float:
    """Both sides are L2-normalized (`normalize_embeddings=True` on every
    embed call in this file), so dot product == cosine similarity -- same
    reasoning `agents.answer.grounding._cosine_similarity` already uses.
    """
    return sum(x * y for x, y in zip(a, b, strict=True))


def _norm(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _evidence_hit(evidence: list[str], chunks: list[ScoredChunk]) -> bool:
    haystacks = [_norm(chunk.content) for chunk in chunks]
    return any(_norm(phrase) in haystack for phrase in evidence for haystack in haystacks)


async def _rank_top_k(
    adapter: ModelAdapter, query: str, corpus: list[ChunkRow], chunk_vectors: list[list[float]], top_k: int
) -> list[ScoredChunk]:
    query_vector = await adapter.embed_query(query)
    similarities = [_dot(query_vector, vector) for vector in chunk_vectors]
    ranked_indices = sorted(range(len(corpus)), key=lambda i: similarities[i], reverse=True)[:top_k]
    return [
        ScoredChunk(
            chunk_id=corpus[i].chunk_id,
            document_id=corpus[i].document_id,
            collection=corpus[i].collection,
            content=corpus[i].content,
            score=similarities[i],
            source_offset_start=corpus[i].source_offset_start,
            source_offset_end=corpus[i].source_offset_end,
            title=corpus[i].title,
            source_url=corpus[i].source_url,
        )
        for i in ranked_indices
    ]


async def _evaluate_question(entry: dict, top_chunks: list[ScoredChunk], llm) -> dict:
    assembled = assemble_context(top_chunks)

    recall_hit = _evidence_hit(entry["evidence"], top_chunks) if "evidence" in entry else None

    raw_answer = await generate_answer(llm, entry["question"], assembled) if assembled else ""
    produced_answer = bool(raw_answer) and not is_no_answer(raw_answer)

    grounded = False
    grounded_ratio = 0.0
    if produced_answer:
        sentences = split_sentences(raw_answer)
        grounded_sentences = await verify_grounding(llm, sentences, assembled)
        grounded_ratio = len(grounded_sentences) / len(sentences) if sentences else 0.0
        grounded = len(grounded_sentences) > 0

    return {
        "id": entry["id"],
        "category": entry["category"],
        "recall_hit": recall_hit,
        "produced_answer": produced_answer,
        "grounded": grounded,
        "grounded_sentence_ratio": grounded_ratio,
        "answer": raw_answer or None,
        "top_chunks": [f"[{c.collection}] {(c.title or str(c.chunk_id))[:60]}" for c in top_chunks],
    }


async def _run_model(adapter: ModelAdapter, corpus: list[ChunkRow], questions: list[dict], top_k: int, llm) -> list[dict]:
    print(f"\nEmbedding {len(corpus)} corpus chunks with {adapter.label} ...", flush=True)
    chunk_vectors = await adapter.embed_texts([row.content for row in corpus])

    results = []
    for entry in questions:
        top_chunks = await _rank_top_k(adapter, entry["question"], corpus, chunk_vectors, top_k)
        result = await _evaluate_question(entry, top_chunks, llm)
        print(
            f"  [{adapter.key:<9}] {entry['id']:<32} {entry['category']:<15} "
            f"recall_hit={result['recall_hit']}  grounded={result['grounded']}",
            flush=True,
        )
        results.append(result)
    return results


# --------------------------------------------------------------------------
# aggregation + reporting
# --------------------------------------------------------------------------


def _mean(values: list[bool | float]) -> float | None:
    values = [float(v) for v in values]
    return sum(values) / len(values) if values else None


def _aggregate(results: list[dict]) -> dict:
    recall_eligible = [r for r in results if r["recall_hit"] is not None]
    by_category: dict[str, list[dict]] = {}
    for r in results:
        by_category.setdefault(r["category"], []).append(r)

    recall_by_category = {
        cat: _mean([r["recall_hit"] for r in rs if r["recall_hit"] is not None])
        for cat, rs in by_category.items()
        if any(r["recall_hit"] is not None for r in rs)
    }
    grounded_by_category = {cat: _mean([r["grounded"] for r in rs]) for cat, rs in by_category.items()}

    return {
        "recall_at_k": _mean([r["recall_hit"] for r in recall_eligible]),
        "recall_at_k_by_category": recall_by_category,
        "grounded_rate_by_category": grounded_by_category,
        # Headline per the module docstring: recall over every question with
        # real evidence, grounding rate over just clear-answer.
        "headline_grounding_rate": grounded_by_category.get("clear-answer"),
    }


def _print_comparison(aggregates: dict[str, dict]) -> None:
    print("\n" + "=" * 78, flush=True)
    print("COMPARISON", flush=True)
    print("=" * 78, flush=True)
    for key, adapter in ((m.key, m) for m in _MODELS):
        agg = aggregates[key]

        def _fmt(value: float | None) -> str:
            return f"{value:.3f}" if value is not None else "n/a"

        print(f"\n{adapter.label}", flush=True)
        print(f"  retrieval recall@k (clear-answer + ambiguous) : {_fmt(agg['recall_at_k'])}", flush=True)
        for cat, value in agg["recall_at_k_by_category"].items():
            print(f"      recall@k [{cat}]{'':<{max(1, 16 - len(cat))}}: {_fmt(value)}", flush=True)
        print(f"  answer-grounding rate (clear-answer)          : {_fmt(agg['headline_grounding_rate'])}", flush=True)
        for cat, value in agg["grounded_rate_by_category"].items():
            print(f"      grounded rate [{cat}]{'':<{max(1, 8 - len(cat))}}: {_fmt(value)}", flush=True)


def _decide(aggregates: dict[str, dict]) -> dict:
    current, candidate = aggregates["current"], aggregates["candidate"]
    recall_delta = (candidate["recall_at_k"] or 0) - (current["recall_at_k"] or 0)
    grounding_delta = (candidate["headline_grounding_rate"] or 0) - (current["headline_grounding_rate"] or 0)

    meaningfully_better = (
        recall_delta >= _MEANINGFUL_IMPROVEMENT_MARGIN or grounding_delta >= _MEANINGFUL_IMPROVEMENT_MARGIN
    ) and recall_delta >= -0.05 and grounding_delta >= -0.05

    return {
        "recall_delta": recall_delta,
        "grounding_delta": grounding_delta,
        "candidate_wins_meaningfully": meaningfully_better,
    }


def _print_decision(decision: dict) -> None:
    print("\n" + "=" * 78, flush=True)
    print("DECISION", flush=True)
    print("=" * 78, flush=True)
    print(f"  recall@k delta (candidate - current)    : {decision['recall_delta']:+.3f}", flush=True)
    print(f"  grounding-rate delta (candidate - current): {decision['grounding_delta']:+.3f}", flush=True)
    if decision["candidate_wins_meaningfully"]:
        print(
            f"\n  Candidate wins meaningfully (>= {_MEANINGFUL_IMPROVEMENT_MARGIN:.2f} margin on at least one "
            "headline metric, no regression on the other beyond noise). Per this script's own "
            "instructions: do NOT switch app/retrieval/embedding.py's model yet -- write a new "
            "numbered entry in docs/ENGINEERING_DECISIONS.md covering the re-embedding migration "
            "and cost/latency tradeoff first, and let that decision authorize the change.",
            flush=True,
        )
    else:
        print(
            f"\n  Candidate does NOT win meaningfully (margin below {_MEANINGFUL_IMPROVEMENT_MARGIN:.2f}, or a "
            "regression on one of the two headline metrics). No migration plan warranted from "
            "this run -- the current model stays.",
            flush=True,
        )


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


async def _run(args) -> dict:
    dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))
    questions = dataset["questions"]
    if args.category:
        questions = [q for q in questions if q["category"] == args.category]
    if args.limit:
        questions = questions[: args.limit]

    org_id, org_name, doc_count = await _resolve_org(args.org_slug)
    print("=" * 78, flush=True)
    print("EKIP -- EMBEDDING MODEL COMPARISON", flush=True)
    print("=" * 78, flush=True)
    print(f"Organization    : {org_name!r} (slug={args.org_slug}, id={org_id})", flush=True)
    print(f"Live documents  : {doc_count}", flush=True)
    print(f"Questions to run: {len(questions)}", flush=True)
    print(f"top_k           : {args.top_k}", flush=True)
    if doc_count == 0:
        raise SystemExit(f"\nOrganization {args.org_slug!r} has 0 ingested documents.")

    corpus = await _fetch_corpus(org_id)
    if not corpus:
        raise SystemExit(f"\nOrganization {args.org_slug!r} has 0 chunks across all collections.")
    print(f"Corpus chunks   : {len(corpus)}", flush=True)

    llm = get_llm(temperature=0.0)

    all_results: dict[str, list[dict]] = {}
    aggregates: dict[str, dict] = {}
    for adapter in _MODELS:
        results = await _run_model(adapter, corpus, questions, args.top_k, llm)
        all_results[adapter.key] = results
        aggregates[adapter.key] = _aggregate(results)

    _print_comparison(aggregates)
    decision = _decide(aggregates)
    _print_decision(decision)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "organization": {"slug": args.org_slug, "name": org_name},
        "top_k": args.top_k,
        "corpus_chunk_count": len(corpus),
        "question_count": len(questions),
        "models": {adapter.key: adapter.label for adapter in _MODELS},
        "aggregates": aggregates,
        "decision": decision,
        "per_question_results": all_results,
    }
    args.report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nFull report written to: {args.report_path}", flush=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare embedding-model retrieval quality against the golden question set."
    )
    parser.add_argument("--org-slug", default=_DEFAULT_ORG_SLUG)
    parser.add_argument("--category", choices=["clear-answer", "ambiguous", "no-information"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--top-k", type=int, default=_DEFAULT_TOP_K)
    parser.add_argument("--report-path", type=Path, default=_DEFAULT_REPORT_PATH)
    args = parser.parse_args()
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
