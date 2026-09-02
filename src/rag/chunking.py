"""청크 분할과 한국어 BM25 토크나이저.

- chunk_text: 문단 경계를 우선하고, 긴 문단은 문장 단위로 잘라 max_chars 안에 맞춘다. 앞 청크 꼬리를
  overlap 만큼 이어 붙여 문맥이 끊기지 않게 한다.
- tokenize: 형태소 분석기 없이 한국어를 다루기 위해 어절 + 어절 내부 문자 bigram 을 함께 낸다
  ("연금저축을" → 연금저축을, 연금, 금저, 저축, 축을). 조사 변화에 강하고 rank_bm25 와 바로 맞는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SENT_SPLIT = re.compile(r"(?<=[.!?。…])\s+|(?<=다\.)\s+|(?<=요\.)\s+")
TOKEN_RE = re.compile(r"[가-힣]+|[a-zA-Z]+|\d+(?:[.,]\d+)?")


@dataclass
class Chunk:
    text: str
    index: int
    meta: dict = field(default_factory=dict)


def _split_long(paragraph: str, max_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]
    sents = [s.strip() for s in SENT_SPLIT.split(paragraph) if s.strip()]
    out, cur = [], ""
    for s in sents:
        if len(s) > max_chars:  # 문장 하나가 너무 길면 강제 절단
            if cur:
                out.append(cur)
                cur = ""
            out += [s[i : i + max_chars] for i in range(0, len(s), max_chars)]
            continue
        if len(cur) + len(s) + 1 > max_chars and cur:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def chunk_text(text: str, max_chars: int = 600, overlap: int = 80, min_chars: int = 40) -> list[Chunk]:
    text = text.replace("\r\n", "\n")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    buf = ""
    for p in paragraphs:
        for part in _split_long(p, max_chars):
            if len(buf) + len(part) + 1 <= max_chars:
                buf = f"{buf}\n{part}".strip()
            else:
                if buf:
                    pieces.append(buf)
                buf = part
    if buf:
        pieces.append(buf)

    chunks: list[Chunk] = []
    prev_tail = ""
    for i, piece in enumerate(pieces):
        body = (prev_tail + "\n" + piece).strip() if prev_tail else piece
        if len(body) >= min_chars or i == len(pieces) - 1:
            chunks.append(Chunk(text=body, index=len(chunks)))
        prev_tail = piece[-overlap:] if overlap and len(piece) > overlap else ""
    return chunks


def tokenize(text: str) -> list[str]:
    toks: list[str] = []
    for m in TOKEN_RE.finditer(text.lower()):
        w = m.group(0)
        toks.append(w)
        if re.fullmatch(r"[가-힣]+", w) and len(w) >= 3:
            toks += [w[i : i + 2] for i in range(len(w) - 1)]
    return toks
