"""도메인 도구 모음.

두 가지 형태로 제공한다.
1) 순수 Python 함수 — 스크립트·테스트·백엔드에서 직접 호출
2) SDK-MCP 도구 (`build_mcp_server()`) — `src/agent.py` 가 Claude Agent SDK 로 오케스트레이션을 돌릴 때
   서브에이전트가 Bash 없이도 같은 기능을 `mcp__blog_agent__*` 도구로 쓸 수 있게 한다 (패턴 5).

대화형 Claude Code 경로에서는 CLAUDE.md 가 지시하는 대로 `scripts/*.py` 를 Bash 로 호출하며, 그 스크립트들도
결국 여기 있는 함수(또는 같은 하위 모듈)를 쓴다.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ──────────────────────────── 1. 자료 수집 ────────────────────────────

def extract_subtitles(url: str, out_dir: str | Path, langs: str = "ko,en") -> dict:
    """yt-dlp 로 자막(수동 → 자동 순)과 제목·채널·길이를 가져온다. researcher 서브에이전트가 하는 일의 함수판."""
    out_dir = Path(out_dir)
    (out_dir / "subs").mkdir(parents=True, exist_ok=True)
    tmpl = str(out_dir / "subs" / "%(id)s")
    meta = subprocess.run([sys.executable, "-m", "yt_dlp", "--print", "%(id)s|%(title)s|%(channel)s|%(duration_string)s",
                           "--skip-download", url], capture_output=True, text=True, check=True).stdout.strip()
    vid, title, channel, duration = (meta.split("|", 3) + ["", "", ""])[:4]
    for flag in ("--write-subs", "--write-auto-subs"):
        subprocess.run([sys.executable, "-m", "yt_dlp", flag, "--sub-langs", langs, "--skip-download",
                        "--convert-subs", "srt", "-o", tmpl, url], capture_output=True, text=True)
        srts = sorted((out_dir / "subs").glob(f"{vid}*.srt"))
        if srts:
            text = clean_srt(srts[0].read_text(encoding="utf-8", errors="replace"))
            (out_dir / "transcript.txt").write_text(text, encoding="utf-8")
            return {"video_id": vid, "title": title, "channel": channel, "duration": duration,
                    "subtitle": "manual" if flag == "--write-subs" else "auto", "transcript": str(out_dir / "transcript.txt")}
    return {"video_id": vid, "title": title, "channel": channel, "duration": duration, "subtitle": None, "transcript": None}


def clean_srt(srt: str) -> str:
    """타임스탬프·번호·자동 자막 특유의 중복 줄 제거."""
    lines, prev = [], ""
    for line in srt.splitlines():
        s = line.strip()
        if not s or s.isdigit() or re.match(r"^\d{2}:\d{2}:\d{2},\d{3} -->", s):
            continue
        s = re.sub(r"<[^>]+>", "", s)
        if s != prev:
            lines.append(s)
            prev = s
    return "\n".join(lines)


# ──────────────────────────── 2. RAG ────────────────────────────

def rag_index(path: str, run: str, kind: str, angle: str | None = None) -> dict:
    from src.retriever import index_file

    p = Path(path)
    files = [p] if p.is_file() else sorted(x for x in p.iterdir() if x.suffix.lower() in {".md", ".txt", ".html"})
    counts = {str(f): index_file(f, run=run, kind=kind, angle=angle) for f in files}
    return {"indexed": counts, "total_chunks": sum(counts.values())}


def rag_search(query: str, run: str | None = None, angle: str | None = None, kind: str | None = None,
               top_k: int = 6, expand: bool = True, rerank: bool = True) -> list[dict]:
    from src.retriever import get_retriever

    r = get_retriever(expand=expand, rerank=rerank)
    hits = r.search(query, run=run, angle=angle, kind=kind, top_k=top_k)
    return [{"ref": h.ref(), "text": h.text, "rrf": round(h.rrf, 4), "rerank": h.rerank,
             "url": h.meta.get("url"), "kind": h.meta.get("kind")} for h in hits]


def check_faithfulness(draft_path: str, run: str, angle: str | None = None, kind: str | None = None) -> dict:
    from src.retriever import faithfulness

    p = Path(draft_path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".html":
        text = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style|svg)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I))
    report = faithfulness(text, draft_name=p.name, run=run, angle=angle, kind=kind)
    m = re.search(r"_v(\d+)\.", p.name)
    out = p.parent / f"faithfulness_v{m.group(1) if m else '0'}.json"
    out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    d = json.loads(report.model_dump_json())
    d["saved"] = str(out)
    return d


# ──────────────────────────── 3. 평가 ────────────────────────────

def parse_review(review_path: str, append_log: bool = True) -> dict:
    """review_vN.md → review_vN.json (+ review_log). scripts/parse_review.py 와 동일 동작."""
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "parse_review.py"), review_path]
    if not append_log:
        cmd.append("--no-log")
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    if r.returncode != 0:
        return {"error": r.stderr.strip()[-500:]}
    return json.loads(r.stdout)


# ──────────────────────────── 4. 이미지·발행 ────────────────────────────

def svg_to_png(images_dir: str, scale: int = 2) -> str:
    r = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "svg_to_png.py"), images_dir, "--scale", str(scale)],
                       capture_output=True, text=True, cwd=REPO_ROOT)
    return (r.stdout + r.stderr).strip()[-1000:]


def tistory_publish(angle_dir: str, blog: str, publish: bool = True) -> str:
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "tistory_post.py"), angle_dir, "--blog", blog]
    if publish:
        cmd.append("--publish")
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    return (r.stdout + r.stderr).strip()[-1000:]


# ──────────────────────────── 5. SDK-MCP 래퍼 ────────────────────────────

def build_mcp_server(name: str = "blog_agent"):
    """Claude Agent SDK 용 인프로세스 MCP 서버. src/agent.py 가 등록한다."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    def _text(obj) -> dict:
        return {"content": [{"type": "text", "text": json.dumps(obj, ensure_ascii=False, indent=2)}]}

    @tool("rag_search", "색인된 자막·공식 출처·리서치 문서에서 근거 청크를 하이브리드 검색한다.",
          {"query": str, "run": str, "angle": str, "kind": str, "top_k": int})
    async def _rag_search(args: dict) -> dict:
        return _text(rag_search(args["query"], run=args.get("run") or None, angle=args.get("angle") or None,
                                kind=args.get("kind") or None, top_k=int(args.get("top_k") or 6)))

    @tool("rag_index", "파일 또는 폴더를 청크로 나눠 RAG 색인에 넣는다.",
          {"path": str, "run": str, "kind": str, "angle": str})
    async def _rag_index(args: dict) -> dict:
        return _text(rag_index(args["path"], run=args["run"], kind=args["kind"], angle=args.get("angle") or None))

    @tool("check_faithfulness", "초안의 사실 주장을 색인된 근거와 대조해 supported/partially/unsupported 를 판정한다.",
          {"draft_path": str, "run": str, "angle": str})
    async def _check_faithfulness(args: dict) -> dict:
        return _text(check_faithfulness(args["draft_path"], run=args["run"], angle=args.get("angle") or None))

    @tool("parse_review", "reviewer 판정문(review_vN.md)을 구조화 JSON 으로 변환하고 판정 로그에 누적한다.",
          {"review_path": str})
    async def _parse_review(args: dict) -> dict:
        return _text(parse_review(args["review_path"]))

    return create_sdk_mcp_server(name=name, tools=[_rag_search, _rag_index, _check_faithfulness, _parse_review])


MCP_TOOL_NAMES = ["mcp__blog_agent__rag_search", "mcp__blog_agent__rag_index",
                  "mcp__blog_agent__check_faithfulness", "mcp__blog_agent__parse_review"]
