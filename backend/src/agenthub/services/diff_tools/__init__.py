"""Unified diff tool."""
from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass


@dataclass
class CodeBlock:
    """Extracted code block."""
    language: str | None
    content: str
    file_path: str | None

@dataclass
class DiffResult:
    """Diff result."""
    relative_path: str
    unified_diff: str
    original_content: str
    proposed_content: str
    content_hash: str
    is_new_file: bool


def extract_code_blocks(text: str) -> list[CodeBlock]:
    """Extract fenced code blocks."""
    pattern = re.compile(
        r"```(?:\s*(\w+)(?:\s+(\S+))?)?\s*\n(.*?)```",
        re.DOTALL,
    )
    blocks: list[CodeBlock] = []
    for match in pattern.finditer(text):
        language = match.group(1) or None
        explicit_path = match.group(2) or None
        raw_content = match.group(3)
        inferred_path: str | None = None
        first_line = raw_content.lstrip("\n").split("\n")[0].strip() if raw_content else ""
        path_bold = re.match(r"\*\*文件路径:\s*(\S+)\*\*", first_line)
        file_comment = re.match(r"^(?://|#)\s*file:\s*(\S+)", first_line)
        if path_bold:
            inferred_path = path_bold.group(1)
            raw_content = raw_content.lstrip("\n").split("\n", 1)[1] if "\n" in raw_content else ""
        elif file_comment:
            inferred_path = file_comment.group(1)
            raw_content = raw_content.lstrip("\n").split("\n", 1)[1] if "\n" in raw_content else ""
        file_path = explicit_path or inferred_path
        if file_path is None:
            file_path = _infer_path_from_code(raw_content, language)
        blocks.append(CodeBlock(
            language=language,
            content=raw_content.strip(),
            file_path=file_path,
        ))
    return blocks


def _infer_path_from_code(code: str, language: str | None) -> str | None:
    """Infer file path from source code."""
    ext_map = {
        "java": ".java", "python": ".py", "py": ".py",
        "typescript": ".ts", "ts": ".ts", "tsx": ".tsx",
        "javascript": ".js", "js": ".js", "jsx": ".jsx",
        "rust": ".rs", "rs": ".rs", "go": ".go",
        "c": ".c", "cpp": ".cpp", "csharp": ".cs", "cs": ".cs",
        "html": ".html", "css": ".css", "sql": ".sql",
        "json": ".json", "yaml": ".yaml", "yml": ".yml",
        "sh": ".sh", "bash": ".sh",
    }
    class_match = re.search(
        r"^\s*(?:public\s+)?(?:abstract\s+)?(?:final\s+)?"
        r"(?:class|interface|enum|record)\s+(\w+)",
        code.strip(), re.MULTILINE,
    )
    if class_match:
        class_name = class_match.group(1)
        if language and language in ext_map:
            return f"{class_name}{ext_map[language]}"
        if re.search(r"import\s+java\.", code) or "public static void main" in code:
            return f"{class_name}.java"
        if "using System;" in code or "namespace " in code:
            return f"{class_name}.cs"
    rust_match = re.search(
        r"^\s*(?:pub\s+)?(?:mod|struct|enum|trait|impl)\s+(\w+)",
        code.strip(), re.MULTILINE,
    )
    if rust_match and language in (None, "rust", "rs"):
        return f"{rust_match.group(1).lower()}.rs"
    if language and language in ext_map:
        return f"code{ext_map[language]}"
    return None


def compute_unified_diff(
    original_content: str,
    proposed_content: str,
    relative_path: str,
) -> DiffResult:
    """Compute unified diff."""
    original_lines = original_content.splitlines(keepends=True)
    proposed_lines = proposed_content.splitlines(keepends=True)
    if original_content and not original_content.endswith("\n"):
        original_lines[-1] = original_lines[-1] + "\n"
    if proposed_content and not proposed_content.endswith("\n"):
        proposed_lines[-1] = proposed_lines[-1] + "\n"
    diff_lines = list(difflib.unified_diff(
        original_lines, proposed_lines,
        fromfile=f"a/{relative_path}", tofile=f"b/{relative_path}", lineterm="",
    ))
    unified_diff_text = "\n".join(diff_lines) + "\n" if diff_lines else ""
    content_hash = hashlib.sha256(unified_diff_text.encode("utf-8")).hexdigest()
    return DiffResult(
        relative_path=relative_path, unified_diff=unified_diff_text,
        original_content=original_content, proposed_content=proposed_content,
        content_hash=content_hash, is_new_file=original_content == "",
    )


def create_file_content_diff(new_content: str, relative_path: str) -> DiffResult:
    """Create diff for new file."""
    return compute_unified_diff("", new_content, relative_path)
