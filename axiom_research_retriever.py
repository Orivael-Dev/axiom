"""Local-file retriever for the Re:Search engine.

Indexes Markdown / text / Python files under a set of roots, scores
each one against a query using a small BM25 variant (pure Python, no
extra deps), and returns the top-K with a snippet centred on the
strongest term hit.

Scope: small repos (≤ ~10k files). Index lives in process memory and
is built once on first call. For larger corpora, swap in a real
vector store — the public interface is small enough that the rest
of the system won't notice.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

# File extensions we'll index. Markdown is the priority signal; .py
# and .txt fill in the corners. PDFs are skipped (no text extraction
# in this build); HTML is skipped (page text is noisy + duplicated).
_INCLUDE_EXTS = {".md", ".markdown", ".txt", ".py", ".rst"}

# Skip paths matching any of these segments anywhere in the path.
_SKIP_SEGMENTS = {
    "__pycache__", ".git", "node_modules", ".pytest_cache",
    ".mypy_cache", "venv", ".venv", "build", "dist",
    "benchmarks", "logs",
}

_MAX_BYTES = 256 * 1024     # ~250 KB per file cap; we're indexing docs


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
_STOPWORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "with", "you", "this",
    "that", "from", "have", "has", "had", "was", "were", "been", "their",
    "they", "them", "what", "who", "when", "where", "why", "how", "which",
    "into", "out", "more", "less", "than", "then", "also", "about",
    "any", "all", "some", "one", "two", "three", "use", "uses", "used",
    "your", "his", "her", "its", "our", "would", "could", "should",
    "will", "can", "may", "might", "must", "just", "very", "much",
    "many", "such", "only", "even", "still", "yet", "now", "here",
    "there", "these", "those",
})


def _tokenize(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)
            if t.lower() not in _STOPWORDS]


def _safe_read(path: Path) -> Optional[str]:
    try:
        if path.stat().st_size > _MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None


@dataclass(frozen=True)
class IndexedDocument:
    path:         Path
    relative:     str          # path relative to the indexer's primary root
    content:      str
    token_count:  int
    token_freq:   dict         # token → frequency in this doc


@dataclass(frozen=True)
class RetrievedSource:
    title:           str
    uri:             str
    kind:            str
    score:           float
    snippet:         str
    # Which provider returned this hit. "local" is the in-repo BM25
    # corpus; external providers ("pubmed", "clinicaltrials",
    # "openfda") fan in via axiom_research_providers.MultiProviderRetriever.
    provider:        str = "local"
    # Evidence tier (1=primary literature/regulator, 5=blocked source),
    # looked up against axiom_medical_safety.EVIDENCE_TIER_REGISTRY by
    # URI host. None for local-corpus hits with no public domain.
    evidence_tier:   Optional[int] = None

    def to_dict(self) -> dict:
        d = {
            "title":   self.title,
            "uri":     self.uri,
            "kind":    self.kind,
            "score":   round(self.score, 4),
            "snippet": self.snippet,
            "provider": self.provider,
        }
        if self.evidence_tier is not None:
            d["evidence_tier"] = self.evidence_tier
        return d


class LocalRetriever:
    """Pure-Python BM25-ish local file retriever.

    Usage:
        r = LocalRetriever(roots=[Path("docs"), Path("README.md")])
        for src in r.retrieve("event token coordinator", k=5):
            print(src.title, src.score)
    """

    # BM25 tuning constants — defaults from the literature work fine
    # on small text corpora.
    _K1 = 1.5
    _B  = 0.75

    def __init__(
        self,
        roots:    Sequence[Path],
        *,
        include_exts: Optional[Iterable[str]] = None,
        max_files:    int = 4000,
    ) -> None:
        self._roots = [Path(p).resolve() for p in roots if p]
        self._include_exts = (set(include_exts) if include_exts is not None
                                                else _INCLUDE_EXTS)
        self._max_files = max_files
        self._docs:    List[IndexedDocument] = []
        self._idf:     dict[str, float] = {}
        self._avg_len: float = 1.0
        self._built = False

    @property
    def primary_root(self) -> Path:
        return self._roots[0] if self._roots else Path.cwd()

    def stats(self) -> dict:
        return {
            "indexed_files":   len(self._docs),
            "unique_tokens":   len(self._idf),
            "avg_token_len":   round(self._avg_len, 1),
            "roots":           [str(r) for r in self._roots],
            "built":           self._built,
        }

    # ── building ─────────────────────────────────────────────────────

    def build(self) -> None:
        """Index every eligible file under the configured roots."""
        if self._built:
            return
        files_seen = 0
        for root in self._roots:
            for path in self._iter_files(root):
                if files_seen >= self._max_files:
                    break
                content = _safe_read(path)
                if content is None:
                    continue
                tokens = _tokenize(content)
                if not tokens:
                    continue
                freq: dict[str, int] = {}
                for t in tokens:
                    freq[t] = freq.get(t, 0) + 1
                try:
                    rel = str(path.relative_to(self.primary_root))
                except ValueError:
                    rel = str(path)
                self._docs.append(IndexedDocument(
                    path=path, relative=rel, content=content,
                    token_count=len(tokens), token_freq=freq,
                ))
                files_seen += 1

        # IDF + average document length for BM25.
        n = len(self._docs)
        if n == 0:
            self._avg_len = 1.0
            self._built = True
            return
        df: dict[str, int] = {}
        total_len = 0
        for d in self._docs:
            total_len += d.token_count
            for t in d.token_freq:
                df[t] = df.get(t, 0) + 1
        self._avg_len = total_len / n
        for t, n_with_t in df.items():
            self._idf[t] = math.log((n - n_with_t + 0.5) / (n_with_t + 0.5) + 1.0)
        self._built = True

    def _iter_files(self, root: Path):
        if root.is_file():
            if root.suffix.lower() in self._include_exts:
                yield root
            return
        if not root.exists():
            return
        for sub in root.rglob("*"):
            if not sub.is_file():
                continue
            if sub.suffix.lower() not in self._include_exts:
                continue
            parts = set(sub.parts)
            if parts & _SKIP_SEGMENTS:
                continue
            yield sub

    # ── querying ─────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        *,
        k: int = 5,
        domain: Optional[str] = None,
    ) -> List[RetrievedSource]:
        # `domain` is accepted (and ignored) on the plain retriever so
        # the call site in axiom_research_server can pass it
        # unconditionally. DomainRoutedRetriever uses it to dispatch.
        del domain
        if not query or not query.strip():
            return []
        self.build()
        if not self._docs:
            return []
        q_terms = _tokenize(query)
        if not q_terms:
            return []
        scored: list[tuple[float, IndexedDocument, str]] = []
        for d in self._docs:
            score = self._score(d, q_terms)
            if score <= 0:
                continue
            snippet = self._snippet(d, q_terms)
            scored.append((score, d, snippet))
        scored.sort(key=lambda x: -x[0])
        out: List[RetrievedSource] = []
        max_score = scored[0][0] if scored else 1.0
        for score, doc, snippet in scored[:k]:
            norm = round(score / max_score, 4) if max_score > 0 else 0.0
            out.append(RetrievedSource(
                title=self._title_for(doc),
                uri=doc.relative,
                kind=self._kind_for(doc),
                score=norm,
                snippet=snippet,
            ))
        return out

    def _score(self, doc: IndexedDocument, q_terms: List[str]) -> float:
        score = 0.0
        for t in q_terms:
            tf = doc.token_freq.get(t, 0)
            if tf == 0:
                continue
            idf = self._idf.get(t, 0.0)
            denom = tf + self._K1 * (
                1 - self._B + self._B * (doc.token_count / self._avg_len)
            )
            score += idf * ((tf * (self._K1 + 1)) / denom)
        return score

    @staticmethod
    def _title_for(doc: IndexedDocument) -> str:
        # First non-empty heading line, or first line, or filename.
        for line in doc.content.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                return s.lstrip("# ").strip()[:120]
            if s.startswith(('"""', "'''")):
                continue
            return s[:120]
        return doc.path.name

    @staticmethod
    def _kind_for(doc: IndexedDocument) -> str:
        ext = doc.path.suffix.lower()
        if ext in (".md", ".markdown"):
            return "internal-doc · md"
        if ext == ".py":
            return "source · py"
        if ext == ".rst":
            return "internal-doc · rst"
        return "internal-text"

    @staticmethod
    def _snippet(doc: IndexedDocument, q_terms: List[str],
                 *, span: int = 200) -> str:
        """Return ~`span` chars of context around the first strong hit."""
        lower = doc.content.lower()
        best_idx = -1
        best_term = ""
        for t in q_terms:
            idx = lower.find(t)
            if idx >= 0 and (best_idx < 0 or idx < best_idx):
                best_idx = idx
                best_term = t
        if best_idx < 0:
            return doc.content.strip()[:span * 2]
        start = max(0, best_idx - span // 2)
        end   = min(len(doc.content), best_idx + len(best_term) + span // 2)
        snippet = doc.content[start:end].replace("\n", " ").strip()
        if start > 0:
            snippet = "…" + snippet
        if end < len(doc.content):
            snippet = snippet + "…"
        # Collapse runs of whitespace.
        return re.sub(r"\s+", " ", snippet)


# ── module-level default retriever (lazy, cached) ────────────────────────


_DEFAULT: Optional[LocalRetriever] = None


class DomainRoutedRetriever:
    """Dispatch retrieval to the right per-domain corpus.

    Holds a `{domain: LocalRetriever}` map + a default fallback. The
    research server calls `retrieve(query, domain=req.domain)` and
    this class routes to the matching corpus. Unknown / missing
    domain falls through to the default — same shape as
    `DomainRoutedBackend` over in axiom_event_token/backends.py.

    Configure via `AXIOM_RETRIEVAL_DIR_<DOMAIN>` env vars discovered
    by `default_retriever()`:

      AXIOM_RETRIEVAL_DIR_MEDICAL=/data/corpora/medical
      AXIOM_RETRIEVAL_DIR_SECURITY=/data/corpora/security
      AXIOM_RETRIEVAL_DIR_FINANCE=/data/corpora/finance

    Comma-separated paths are allowed for multi-root corpora:

      AXIOM_RETRIEVAL_DIR_MEDICAL=/data/pubmed,/data/clinical-guides
    """

    def __init__(
        self,
        default: "LocalRetriever",
        per_domain: Optional[dict] = None,
    ) -> None:
        if default is None:
            raise ValueError("DomainRoutedRetriever requires a default retriever")
        self._default = default
        self._per_domain: dict[str, LocalRetriever] = {
            k.lower(): v for k, v in (per_domain or {}).items() if v is not None
        }

    @property
    def primary_root(self) -> Path:
        # Defer to the default — primary_root is used by stats / debug,
        # not by routing logic.
        return self._default.primary_root

    def stats(self) -> dict:
        return {
            "kind":    "domain-routed",
            "default": self._default.stats(),
            "per_domain": {
                d: r.stats() for d, r in sorted(self._per_domain.items())
            },
        }

    def retrieve(
        self,
        query: str,
        *,
        k: int = 5,
        domain: Optional[str] = None,
    ) -> List[RetrievedSource]:
        d = (domain or "").strip().lower()
        target = self._per_domain.get(d, self._default) if d else self._default
        return target.retrieve(query, k=k)


# Domains the research console supports. Mirrors ROUTED_DOMAINS in
# axiom_event_token/backends.py so backend + retriever per-domain
# configs share the same vocabulary.
ROUTED_DOMAINS = ("general", "medical", "finance", "security", "hr",
                  "supply_chain")


def _per_domain_dirs(domain: str) -> List[Path]:
    """Return the configured corpus roots for `domain`, or [] if no
    override is set. Comma-separated AXIOM_RETRIEVAL_DIR_<DOMAIN>
    values map to multiple roots."""
    raw = os.environ.get(f"AXIOM_RETRIEVAL_DIR_{domain.upper()}")
    if not raw:
        return []
    paths: list[Path] = []
    for chunk in raw.split(","):
        s = chunk.strip()
        if not s:
            continue
        p = Path(s).expanduser()
        if not p.exists():
            # Don't fail the boot — just log+skip. Stats will show the
            # corpus didn't get indexed, so operators can spot the typo.
            import logging
            logging.getLogger("axiom.retriever").warning(
                "AXIOM_RETRIEVAL_DIR_%s points at %s which doesn't exist; "
                "skipping. Fix the path and restart.",
                domain.upper(), p,
            )
            continue
        paths.append(p)
    return paths


def default_retriever(repo_root: Optional[Path] = None) -> "LocalRetriever | DomainRoutedRetriever":
    """Module-level default retriever.

    Indexes the repo's `docs/`, `README.md`, and `patents/` (if
    present) by default. When any `AXIOM_RETRIEVAL_DIR_<DOMAIN>` env
    var is set, wraps the default in a DomainRoutedRetriever so a
    Medical request retrieves from the medical corpus, a Security
    request from the security corpus, etc. No change for deployments
    without per-domain overrides.
    """
    global _DEFAULT
    if _DEFAULT is not None:
        return _DEFAULT
    root = repo_root or Path(__file__).resolve().parent
    base_roots = [
        root / "docs",
        root / "README.md",
        root / "patents",     # patent PDFs are skipped; companion .md if any
    ]
    base = LocalRetriever(roots=[r for r in base_roots if r.exists()])

    per_domain: dict[str, LocalRetriever] = {}
    for d in ROUTED_DOMAINS:
        dirs = _per_domain_dirs(d)
        if dirs:
            per_domain[d] = LocalRetriever(roots=dirs)

    if per_domain:
        local_inner: "LocalRetriever | DomainRoutedRetriever" = \
            DomainRoutedRetriever(default=base, per_domain=per_domain)
    else:
        local_inner = base

    # Wrap in MultiProviderRetriever so external APIs (PubMed,
    # ClinicalTrials, openFDA) fan in alongside the local corpus.
    # Off-switch for air-gapped deployments + the test fixture:
    # AXIOM_EXTERNAL_RETRIEVAL=0 returns the local-only retriever.
    if os.environ.get("AXIOM_EXTERNAL_RETRIEVAL", "1") == "0":
        _DEFAULT = local_inner
        return _DEFAULT

    # Lazy import so axiom_research_retriever still works on its own
    # when the providers package isn't on the path (e.g. minimal builds).
    try:
        from axiom_research_providers.local import LocalCorpusProvider
        from axiom_research_providers.multi import MultiProviderRetriever
        from axiom_research_providers.pubmed import PubMedProvider
        from axiom_research_providers.clinicaltrials import ClinicalTrialsProvider
        from axiom_research_providers.openfda import OpenFDAProvider
    except ImportError as e:
        import logging
        logging.getLogger("axiom.retriever").warning(
            "External retrieval requested but providers package "
            "unavailable (%s); falling back to local-only.", e,
        )
        _DEFAULT = local_inner
        return _DEFAULT

    _DEFAULT = MultiProviderRetriever([
        LocalCorpusProvider(local_inner),
        PubMedProvider(),
        ClinicalTrialsProvider(),
        OpenFDAProvider(),
    ])
    return _DEFAULT
