"""문서 → 청크 → Titan 임베딩 → Chroma 색인 (패턴 3 · RAG).

    python scripts/rag_index.py <파일|폴더> --run {date} [--angle a{N}|g{N}] --kind {transcript|insights|research|source|post|draft}
                                [--title "…"] [--url …] [--max-chars 600] [--overlap 80] [--json]

- 파일: 그 파일 하나를 색인. 폴더: 안의 *.md/*.txt/*.html 을 각각 색인 (source 는 파일별).
- `.html` 은 태그를 벗겨 본문 텍스트만 색인한다 (post_tistory.html → kind=post, 과거 글 중복 회피용).
- `.md` 맨 위에 `<!-- url: … / title: … / org: … / checked: … -->` 주석이 있으면 메타데이터로 흡수한다
  (guide-researcher 가 sources/ 에 저장하는 형식).
- 같은 source 를 다시 색인하면 옛 청크를 지우고 덮어쓴다.

사용 예:
    python scripts/rag_index.py posts/2026-09-02/transcript.txt --run 2026-09-02 --kind transcript
    python scripts/rag_index.py posts/2026-09-02/g1/sources --run 2026-09-02 --angle g1 --kind source
    python scripts/rag_index.py posts/2026-09-02/g1/research.md --run 2026-09-02 --angle g1 --kind research
    python scripts/rag_index.py posts/2026-09-02/a2/post_tistory.html --run 2026-09-02 --angle a2 --kind post
"""

import argparse
import json
import re
import sys
from html import unescape
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.rag.chunking import chunk_text  # noqa: E402
from src.rag.store import KINDS, RagStore  # noqa: E402

META_COMMENT = re.compile(r"^\s*<!--(.*?)-->", re.S)
EXTS = {".md", ".txt", ".html", ".htm"}


def rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def html_to_text(html: str) -> str:
    html = re.sub(r"<(script|style|svg)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"</(p|h[1-6]|li|div|section|article|figure|figcaption|tr|blockquote)>", "\n\n", html, flags=re.I)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    return re.sub(r"[ \t]+", " ", text)


def parse_meta(text: str) -> tuple[dict, str]:
    m = META_COMMENT.match(text)
    if not m:
        return {}, text
    meta = {}
    # 줄바꿈 또는 " / " 구분 (URL 안의 "/" 는 건드리지 않는다)
    for line in re.split(r"\n|\s/\s", m.group(1)):
        if ":" in line:
            k, v = line.split(":", 1)
            k, v = k.strip().lower(), v.strip()
            if k in ("url", "title", "org", "checked", "tags", "meta") and v:
                meta[k] = v
    return meta, text[m.end():]


def load_text(path: Path) -> tuple[str, dict]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_meta(raw)
    if path.suffix.lower() in (".html", ".htm"):
        m = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S | re.I)
        if m and "title" not in meta:
            meta["title"] = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        body = html_to_text(body)
    return body, meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="파일 또는 폴더")
    ap.add_argument("--run", required=True, help="실행 폴더 날짜 (예: 2026-09-02)")
    ap.add_argument("--angle", default=None, help="a{N} / g{N} (공유 자료면 생략)")
    ap.add_argument("--kind", required=True, choices=KINDS)
    ap.add_argument("--title", default=None)
    ap.add_argument("--url", default=None)
    ap.add_argument("--max-chars", type=int, default=600)
    ap.add_argument("--overlap", type=int, default=80)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    target = Path(args.target)
    if not target.exists():
        print(f"경로 없음: {target}", file=sys.stderr)
        sys.exit(1)
    files = [target] if target.is_file() else sorted(p for p in target.iterdir() if p.suffix.lower() in EXTS)
    if not files:
        print(f"색인할 파일 없음 (*.md/*.txt/*.html): {target}", file=sys.stderr)
        sys.exit(1)

    store = RagStore()
    results = []
    for f in files:
        body, meta = load_text(f)
        chunks = chunk_text(body, max_chars=args.max_chars, overlap=args.overlap)
        n = store.index_chunks(
            chunks, run=args.run, angle=args.angle, kind=args.kind, source=rel(f),
            title=args.title or meta.get("title") or f.stem, url=args.url or meta.get("url"),
            extra={k: v for k, v in meta.items() if k in ("org", "checked")},
        )
        results.append({"source": rel(f), "chunks": n, "chars": len(body), "title": args.title or meta.get("title") or f.stem})

    total = sum(r["chunks"] for r in results)
    if args.json:
        print(json.dumps({"indexed": results, "total_chunks": total, "db": str(store.path), "collection_count": store.count()}, ensure_ascii=False))
    else:
        for r in results:
            print(f"색인: {r['source']} → {r['chunks']}청크 ({r['chars']}자) [{r['title']}]")
        print(f"합계 {total}청크 · 컬렉션 전체 {store.count()}청크 · DB {store.path}")


if __name__ == "__main__":
    main()
