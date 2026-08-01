"""分块策略模块——按文件类型采用最优分块方案。

代码按函数/类边界（AST 优先），文档按语义段落+标题边界，
结构化数据按行组。统一返回 Chunk 数据类。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Chunk:
    """分块结果——清洗后文本的一个片段。"""

    content: str
    index: int
    metadata: dict[str, Any] = field(default_factory=dict)


# ── 分块常量 ────────────────────────────────────────────────────────────────────

_CODE_CHUNK_SIZE = 1500  # 代码块上限（字符）
_CODE_CHUNK_OVERLAP = 200  # 代码块重叠
_DOC_CHUNK_SIZE = 2000  # 文档块上限
_DOC_CHUNK_OVERLAP = 200  # 文档块重叠
_XLSX_ROWS_PER_CHUNK = 50  # Excel/CSV 每块行数


def chunk_content(content: str, file_type: str, base_metadata: dict[str, Any] | None = None) -> list[Chunk]:
    """根据文件类型分发分块策略。

    Args:
        content: clean_content 清洗后的纯文本。
        file_type: 文件类型标识。
        base_metadata: 基础元数据（文件路径等），每块继承。

    Returns:
        Chunk 列表，保证 index 连续递增。
    """
    meta = base_metadata or {}
    _normalized = file_type.lower().lstrip(".")

    if _normalized in {"xlsx", "xls", "csv"}:
        return _chunk_structured(content, meta)
    if _is_code_type(_normalized):
        return _chunk_code(content, meta)
    # 文档类（md / pdf / docx / html / txt 等）
    return _chunk_document(content, meta)


# ── 代码分块（AST 优先）──────────────────────────────────────────────────────────


def _chunk_code(text: str, meta: dict[str, Any]) -> list[Chunk]:
    """Python AST 按函数/类边界分块；其他语言回退正则。"""
    if meta.get("language") in (None, "python", "py"):
        try:
            return _chunk_code_ast(text, meta)
        except SyntaxError:
            pass
    return _chunk_code_regex(text, meta)


def _chunk_code_ast(text: str, meta: dict[str, Any]) -> list[Chunk]:
    """Python AST 遍历顶级函数/类定义，按边界切分。"""
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    chunks: list[Chunk] = []
    chunk_buffer: list[str] = []
    current_size = 0

    def flush() -> None:
        nonlocal current_size
        if chunk_buffer:
            chunks.append(Chunk(
                content="".join(chunk_buffer),
                index=len(chunks),
                metadata=dict(meta),
            ))
            chunk_buffer.clear()
            current_size = 0

    for node in ast.iter_child_nodes(tree):
        node_text = "".join(lines[node.lineno - 1: node.end_lineno])  # type: ignore[attr-defined]
        node_size = len(node_text)
        if current_size + node_size > _CODE_CHUNK_SIZE and chunk_buffer:
            flush()
        chunk_buffer.append(node_text)
        current_size += node_size
    flush()
    return chunks


def _chunk_code_regex(text: str, meta: dict[str, Any]) -> list[Chunk]:
    """正则回退——按函数/类定义行大致边界切分。"""
    # 匹配各类语言的函数/类/接口定义行起始
    pattern = re.compile(
        r"^(\s*)(?:"
        r"(export\s+)?(default\s+)?"
        r"(async\s+)?(function\s+|class\s+|interface\s+|enum\s+|struct\s+|impl\s+|trait\s+|"
        r"public\s+(?:static\s+)?(?:async\s+)?(?:class|interface|void|int|string|bool|float|double)\s+|"
        r"private\s+(?:static\s+)?(?:async\s+)?(?:class|void|int|string|bool|float|double)\s+|"
        r"protected\s+(?:static\s+)?(?:async\s+)?(?:class|void|int|string|bool|float|double)\s+|"
        r"def\s+|fn\s+|func\s+)"
        r")",
        re.MULTILINE,
    )
    return _chunk_by_delimiter(text, pattern, meta)


# ── 文档分块（语义段落 + 标题边界）───────────────────────────────────────────────


def _chunk_document(text: str, meta: dict[str, Any]) -> list[Chunk]:
    """Markdown/PDF/DOCX/HTML/TXT ——按 ## 标题和双换行段落边界分块。"""
    # 尝试按 Markdown 标题切分
    heading_pattern = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE)
    if heading_pattern.search(text):
        return _chunk_by_delimiter(text, heading_pattern, meta)
    # 按空行切分段落，再合并至上限
    paragraphs = re.split(r"\n\s*\n", text)
    return _merge_paragraphs(paragraphs, meta)


def _chunk_structured(text: str, meta: dict[str, Any]) -> list[Chunk]:
    """Excel/CSV ——按固定行数分块，保留首行作为上下文。"""
    lines = text.split("\n")
    chunks: list[Chunk] = []
    header_line = lines[0] if lines else ""
    # 第二行开始是数据行
    data_lines = lines[1:] if len(lines) > 1 else []
    for idx in range(0, len(data_lines), _XLSX_ROWS_PER_CHUNK):
        batch = data_lines[idx: idx + _XLSX_ROWS_PER_CHUNK]
        # 每块前面附上表头
        chunk_text = header_line + "\n" + "\n".join(batch) if header_line else "\n".join(batch)
        chunk_meta = dict(meta, row_start=idx + 1, row_end=idx + len(batch))
        chunks.append(Chunk(content=chunk_text, index=len(chunks), metadata=chunk_meta))
    return chunks


# ── 通用工具 ─────────────────────────────────────────────────────────────────────


def _chunk_by_delimiter(
    text: str, pattern: re.Pattern[str], meta: dict[str, Any]
) -> list[Chunk]:
    """按正则分割点切分，合并片段至上限。"""
    splits = pattern.split(text)
    if not splits or not splits[0].strip():
        splits = splits[1:] if len(splits) > 1 else splits
    return _merge_segments(splits, meta, _DOC_CHUNK_SIZE, _DOC_CHUNK_OVERLAP)


def _merge_paragraphs(paragraphs: list[str], meta: dict[str, Any]) -> list[Chunk]:
    """合并段落至上限制。"""
    return _merge_segments(paragraphs, meta, _DOC_CHUNK_SIZE, _DOC_CHUNK_OVERLAP)


def _merge_segments(
    segments: list[str],
    meta: dict[str, Any],
    max_size: int,
    overlap: int,
) -> list[Chunk]:
    """通用合并引擎——贪心拼接至 max_size，超出时新建块并保留 overlap。"""
    chunks: list[Chunk] = []
    buffer: list[str] = []
    current_size = 0

    for seg in segments:
        if seg is None:
            continue
        seg = seg.rstrip()
        if not seg:
            continue
        seg_size = len(seg)
        # 单段超出上限：强行单块
        if seg_size > max_size:
            if buffer:
                chunks.append(Chunk(content="\n\n".join(buffer), index=len(chunks), metadata=dict(meta)))
                buffer = []
                current_size = 0
            chunks.append(Chunk(content=seg, index=len(chunks), metadata=dict(meta)))
            continue
        if current_size + seg_size + 2 > max_size and buffer:
            chunks.append(Chunk(content="\n\n".join(buffer), index=len(chunks), metadata=dict(meta)))
            # 保留重叠：取最后 overlap 字符对应的片段
            overlap_text = "\n\n".join(buffer)[-overlap:] if overlap > 0 else ""
            buffer = [overlap_text] if overlap_text.strip() else []
            current_size = len(overlap_text) if overlap_text.strip() else 0
        buffer.append(seg)
        current_size += seg_size + 2  # +2 for \n\n
    if buffer:
        chunks.append(Chunk(content="\n\n".join(buffer), index=len(chunks), metadata=dict(meta)))
    return chunks


def _is_code_type(file_type: str) -> bool:
    """判断是否为代码文件类型。"""
    _code_types = frozenset({
        "py", "ts", "tsx", "js", "jsx", "rs", "go", "java", "kt", "swift",
        "c", "cpp", "cxx", "h", "hpp", "cs", "rb", "php", "scala", "clj",
        "sql", "graphql", "proto", "vue", "svelte", "astro",
        "css", "scss", "less", "sh", "bash", "zsh", "ps1", "dockerfile",
    })
    return file_type.lower() in _code_types