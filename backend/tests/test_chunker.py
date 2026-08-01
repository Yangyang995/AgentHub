"""分块模块测试。"""

import pytest
from agenthub.rag.knowledge.chunker import chunk_content


class TestChunkDocument:
    def test_markdown(self):
        """Markdown 按标题或段落分块。"""
        text = "## 标题1\n段落A\n\n## 标题2\n段落B"
        chunks = chunk_content(text, "md")
        assert len(chunks) >= 1
        combined = chunks[0].content
        # heading 作为分隔符被 split 消费，内容部分是段落文本
        assert "段落A" in combined
        assert "段落B" in combined

    def test_plain_text_by_paragraph(self):
        """纯文本按段落分块。"""
        text = "段落A。\n\n段落B。\n\n段落C。"
        chunks = chunk_content(text, "txt")
        assert len(chunks) >= 1

    def test_empty_content(self):
        """空内容不分块。"""
        chunks = chunk_content("", "md")
        assert len(chunks) == 0

    def test_single_paragraph(self):
        """单段落内容分块。"""
        text = "这是单段落内容，只有一段。"
        chunks = chunk_content(text, "md")
        assert len(chunks) == 1
        assert "这是单段落内容" in chunks[0].content


class TestChunkCode:
    def test_python_code(self):
        """Python 代码分块。"""
        code = "def a():\n    pass\n\n\ndef b():\n    pass\n"
        chunks = chunk_content(code, "py", {"language": "python"})
        assert len(chunks) >= 1
        combined = chunks[0].content
        assert "def a" in combined
        assert "def b" in combined

    def test_python_class(self):
        """Python 类定义分块。"""
        code = "class MyClass:\n    def method1(self):\n        pass\n"
        chunks = chunk_content(code, "py", {"language": "python"})
        assert len(chunks) >= 1

    def test_javascript_regex(self):
        """JavaScript 代码正则分块。"""
        code = (
            "function hello() {\n  return 'hi';\n}\n\n"
            "function world() {\n  return 'earth';\n}\n"
        )
        chunks = chunk_content(code, "js", {"language": "javascript"})
        assert len(chunks) >= 1

    def test_metadata_preserved(self):
        """元数据在分块中保留。"""
        code = "def test():\n    pass\n"
        chunks = chunk_content(code, "py", {"file_path": "/test.py", "language": "python"})
        for chunk in chunks:
            assert chunk.metadata.get("file_path") == "/test.py"
            assert chunk.metadata.get("language") == "python"


class TestChunkStructured:
    def test_csv_chunking(self):
        """CSV 按行组分块。"""
        lines = ["name: Alice | age: 30"]
        for i in range(100):
            lines.append("name: User{0} | age: {1}".format(i, 20 + i % 50))
        text = "\n".join(lines)
        chunks = chunk_content(text, "csv")
        assert len(chunks) >= 2

    def test_xlsx_chunking(self):
        """XLSX 同 CSV 策略。"""
        text = "表头\n" + "\n".join("row{0}".format(i) for i in range(60))
        chunks = chunk_content(text, "xlsx")
        assert len(chunks) >= 1


class TestChunkIndices:
    def test_indices_sequential(self):
        """分块索引连续递增。"""
        text = "\n\n".join("段落{0}".format(i) for i in range(5))
        chunks = chunk_content(text, "txt")
        indices = [c.index for c in chunks]
        assert indices == list(range(len(indices)))