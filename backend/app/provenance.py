"""Owned source snapshots; hashes prove continuity, not truth."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from uuid import uuid4
from .schemas import SourceSnapshot, EvidencePassage, FindingCitation, utcnow

MAX_SOURCE_CHARS = 200_000
MAX_SNAPSHOT_BYTES = 1_600_000
MAX_PASSAGE_CHARS = 1200


def _root(data_dir: Path) -> Path:
    root = Path(data_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _index(root: Path):
    path = root / "source-index.sqlite3"
    if path.is_symlink():
        raise ValueError("来源索引不允许符号链接")
    con = sqlite3.connect(path, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS snapshots(path TEXT PRIMARY KEY, hash TEXT NOT NULL, file_hash TEXT NOT NULL, stored_at TEXT NOT NULL)")
    return con


def _source_path(root: Path, relative: str) -> Path:
    if not re.fullmatch(r"sources-v1/[0-9a-f]{32}\.json", relative):
        raise ValueError("不是服务端来源快照路径")
    path = root / relative
    if path.is_symlink() or (root / "sources-v1").is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("来源路径越界或使用了符号链接")
    return path


def save_snapshot(text: str, metadata: dict, data_dir: Path) -> SourceSnapshot:
    root = _root(data_dir)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    truncated = len(normalized) > MAX_SOURCE_CHARS
    normalized = normalized[:MAX_SOURCE_CHARS]
    if len(json.dumps(metadata, ensure_ascii=False, default=str).encode()) > 32768:
        raise ValueError("来源元数据过大")
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    relative = f"sources-v1/{uuid4().hex}.json"
    path = _source_path(root, relative)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    snapshot = SourceSnapshot(text=normalized, snapshot_hash=digest, snapshot_path=relative,
                              stored_at=utcnow(), metadata=metadata, content_truncated=truncated)
    raw = snapshot.model_dump_json().encode()
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise ValueError("来源快照过大")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".snapshot-", delete=False) as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())
    os.replace(f.name, path)
    with _index(root) as con:
        con.execute("INSERT INTO snapshots VALUES(?,?,?,?)", (relative, digest,
            hashlib.sha256(raw).hexdigest(), snapshot.stored_at.isoformat()))
    return snapshot


def load_snapshot(evidence, data_dir: Path) -> SourceSnapshot:
    root = _root(data_dir)
    if not evidence.snapshot_path or not evidence.snapshot_hash:
        raise ValueError("旧版材料没有已登记的正文快照")
    path = _source_path(root, evidence.snapshot_path)
    with _index(root) as con:
        row = con.execute("SELECT * FROM snapshots WHERE path=?", (evidence.snapshot_path,)).fetchone()
    if not row or row["hash"] != evidence.snapshot_hash:
        raise ValueError("来源快照未登记或哈希不一致")
    with path.open("rb") as f:
        raw = f.read(MAX_SNAPSHOT_BYTES+1)
    if len(raw) > MAX_SNAPSHOT_BYTES or hashlib.sha256(raw).hexdigest() != row["file_hash"]:
        raise ValueError("来源快照文件哈希不一致")
    snapshot = SourceSnapshot.model_validate_json(raw)
    if hashlib.sha256(snapshot.text.encode()).hexdigest() != evidence.snapshot_hash:
        raise ValueError("来源正文哈希不一致")
    if snapshot.snapshot_path != evidence.snapshot_path or snapshot.stored_at.isoformat() != row["stored_at"]:
        raise ValueError("来源快照版本元数据不一致")
    return snapshot


def split_passages(snapshot: SourceSnapshot) -> list[EvidencePassage]:
    passages = []
    for match in re.finditer(r"[^\n]+", snapshot.text):
        start, end = match.span()
        if not match.group().strip():
            continue
        while start < end:
            stop = min(start + MAX_PASSAGE_CHARS, end)
            if stop < end:
                boundaries = list(re.finditer(r"[。！？.!?；;]", snapshot.text[start:stop]))
                if boundaries:
                    stop = start + boundaries[-1].end()
            passages.append(EvidencePassage(paragraph_id=f"B{len(passages)+1:06}", start=start, end=stop,
                text=snapshot.text[start:stop], snapshot_hash=snapshot.snapshot_hash))
            start = stop
    return passages


def text_terms(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9_]{2,}", text.lower()))
    for phrase in re.findall(r"[\u3400-\u9fff]+", text):
        words.update(phrase[i:i+2] for i in range(len(phrase)-1))
    return words


_NAVIGATION_LINE = re.compile(
    r"^(?:skip to content|navigation menu|sign in(?: appearance settings)?|sign up|appearance settings)$"
    r"|^view all(?: features| use cases| industries| solutions| topics)?$"
    r"|^platform AI code creation\b|^solutions by company size\b"
    r"|^resources explore by (?:topic|type)\b|^enterprise enterprise solutions\b"
    r"|^(?:developer workflows|application security|support & services|by use case|by industry|available add-ons)$"
    r"|^©\s*\d{4}\s+[^.!?。！？]{1,80}(?:Inc\.)?$", re.I,
)


def _navigation_label(text: str) -> bool:
    # Whole prose sentences, including articles discussing copyright or login,
    # are not menu labels merely because they contain one of those words.
    return len(text) <= 180 and bool(_NAVIGATION_LINE.search(text))


def select_passages(passages: list[EvidencePassage], query_texts: list[str], *, limit: int = 2400) -> list[EvidencePassage]:
    """Rank intact source passages; weak cross-language matches must not favor menus."""
    terms = text_terms(" ".join(query_texts))
    passage_terms = {p.paragraph_id: text_terms(p.text) for p in passages}
    frequency = {term: sum(term in items for items in passage_terms.values()) for term in terms}
    def score(p):
        content = p.text.strip()
        relevance = sum((.15 if term.isdigit() or term == "ai" else 1) / (1 + frequency[term]) ** .5
                        for term in passage_terms[p.paragraph_id] & terms)
        # Prefer prose over many tiny navigation labels when language differs or
        # lexical overlap is only a date/AI. This changes ranking, never offsets.
        substance = min(len(content) / 240, 1.0) + (.4 if re.search(r"[.!?。！？][\s\"”']*$", content) else 0)
        return relevance + substance - (10 if _navigation_label(content) else 0)
    # Older snapshots may split a long paragraph at a decimal point. Keep the
    # original IDs/offsets, but never present just "4%" from a split "96.4%".
    groups = []
    for passage in sorted(passages, key=lambda p: p.start):
        previous = groups[-1][-1] if groups else None
        if (previous is not None and previous.end == passage.start
                and re.search(r"\d\.$", previous.text) and re.match(r"\d", passage.text)):
            groups[-1].append(passage)
        else:
            groups.append([passage])
    ranked = sorted(groups, key=lambda group: (-max(score(p) for p in group), group[0].start))
    selected, used = [], 0
    for group in ranked:
        size = sum(len(p.text) for p in group)
        if used + size <= limit:
            selected.extend(group); used += size
        if used >= limit:
            break
    return sorted(selected, key=lambda p: p.start)


def resolve_citation(candidate, evidence, allowed_passages) -> FindingCitation:
    if candidate.evidence_id != evidence.id or candidate.snapshot_hash != evidence.snapshot_hash:
        raise ValueError("引用来源或快照哈希不匹配")
    paragraph = next((p for p in allowed_passages if p.paragraph_id == candidate.paragraph_id), None)
    if paragraph is None or paragraph.snapshot_hash != candidate.snapshot_hash:
        raise ValueError("引用段落未提供给模型")
    position = paragraph.text.find(candidate.quote)
    if position < 0:
        raise ValueError("引文不在指定原文段落中")
    if paragraph.text.find(candidate.quote, position+1) >= 0:
        raise ValueError("引文在段落中出现多次，请提供更完整的上下文")
    return FindingCitation(**candidate.model_dump(), start=paragraph.start+position,
                           end=paragraph.start+position+len(candidate.quote))
