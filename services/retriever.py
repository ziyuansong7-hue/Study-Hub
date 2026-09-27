from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from models import Document, DocumentChunk
from schemas import Evidence


@dataclass(frozen=True)
class SearchResult:
    chunk_id: int
    score: float
    evidence: Evidence


class BaseRetriever(ABC):
    @abstractmethod
    def search(self, course_id: int, query: str, limit: int = 5) -> list[SearchResult]:
        raise NotImplementedError


class KeywordRetriever(BaseRetriever):
    """Small, replaceable lexical retriever suitable for an MVP corpus."""

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _tokens(text: str) -> set[str]:
        stopwords = {
            "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
            "in", "is", "it", "may", "of", "on", "or", "the", "to", "what", "when",
            "which", "with",
        }
        tokens = {
            token for token in re.findall(r"[a-z0-9]+", text.casefold())
            if len(token) > 1 and token not in stopwords
        }
        # Chinese does not use spaces, so Latin-only tokenization made Chinese
        # course material effectively unsearchable. Character bigrams provide
        # a small deterministic fallback without adding a segmentation library.
        for sequence in re.findall(r"[\u3400-\u9fff]+", text):
            if len(sequence) <= 2:
                tokens.add(sequence)
            else:
                tokens.update(sequence[index:index + 2] for index in range(len(sequence) - 1))
                if len(sequence) <= 8:
                    tokens.add(sequence)
        return tokens

    def search(self, course_id: int, query: str, limit: int = 5) -> list[SearchResult]:
        query_tokens = self._tokens(query)
        if not query_tokens:
            return []
        # Let SQLite narrow a large textbook before materializing content in
        # Python. The old implementation loaded every chunk and could exhaust
        # memory on a 500-page book. Prefer longer, more distinctive terms and
        # cap the candidate set; final scoring remains deterministic below.
        search_tokens = sorted(query_tokens, key=lambda value: (-len(value), value))[:8]
        match_expressions = [
            func.lower(DocumentChunk.content).like(f"%{token}%")
            for token in search_tokens
        ]
        sql_score = sum(
            (case((expression, 1), else_=0) for expression in match_expressions),
            start=0,
        )
        rows = self.session.execute(
            select(
                DocumentChunk.id,
                DocumentChunk.content,
                DocumentChunk.section,
                DocumentChunk.page,
                Document.filename,
            )
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                Document.course_id == course_id,
                Document.status == "processed",
                DocumentChunk.usable_for_generation.is_(True),
                or_(*match_expressions),
            )
            .order_by(sql_score.desc(), DocumentChunk.id)
            .limit(min(250, max(80, limit * 40)))
        ).all()
        ranked: list[SearchResult] = []
        for chunk_id, content, section, page, filename in rows:
            content_tokens = self._tokens(f"{section or ''} {content}")
            overlap = query_tokens & content_tokens
            minimum_overlap = 1 if len(query_tokens) <= 2 else 2
            if len(overlap) < minimum_overlap:
                continue
            score = len(overlap) / max(len(query_tokens), 1)
            sentences = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+|\n+", content) if part.strip()]
            sentence_scores = [
                (len(self._tokens(sentence) & query_tokens), index, sentence)
                for index, sentence in enumerate(sentences)
            ]
            best_sentences = [
                sentence for overlap_count, _index, sentence
                in sorted(sentence_scores, key=lambda item: (-item[0], item[1]))[:2]
                if overlap_count > 0
            ]
            quote = " ".join(best_sentences)[:500] or content[:500]
            ranked.append(SearchResult(
                chunk_id=chunk_id,
                score=score,
                evidence=Evidence(
                    document=filename,
                    page=page,
                    section=section,
                    quote=quote,
                ),
            ))
        ordered = sorted(ranked, key=lambda item: (-item.score, item.chunk_id))
        if not ordered:
            return []
        # Do not pad evidence with merely adjacent chunks. Keep results reasonably
        # close to the best lexical match; a single precise citation is preferable.
        relative_floor = max(0.5, ordered[0].score * 0.75)
        return [item for item in ordered if item.score >= relative_floor][:limit]
