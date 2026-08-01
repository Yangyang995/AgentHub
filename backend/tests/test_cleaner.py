"""清洗模块测试——覆盖各文件类型的清洗输出。"""

import pytest
from agenthub.rag.knowledge.cleaner import clean_content


class TestCleanMarkdown:
    def test_normalizes_headings(self):
        """规范化标题空格。"""
        result = clean_content("##  多余空格\n内容".encode("utf-8"), "md")
        assert "## 多余空格" in result.text

    def test_removes_excess_blank_lines(self):
        """去除多余空行。"""
        result = clean_content("段落1\n\n\n\n\n段落2".encode("utf-8"), "md")
        assert "\n\n\n\n" not in result.text
        assert "段落1" in result.text
        assert "段落2" in result.text


class TestCleanText:
    def test_preserves_content(self):
        """纯文本保留基本内容。"""
        result = clean_content(b"hello world", "txt")
        assert "hello world" in result.text

    def test_handles_utf8(self):
        """UTF-8 编码正确处理。"""
        result = clean_content("你好世界".encode("utf-8"), "txt")
        assert "你好世界" in result.text

    def test_json_content(self):
        """JSON 文件清洗。"""
        result = clean_content(b'{"key": "value"}', "json")
        assert "key" in result.text

    def test_yaml_content(self):
        """YAML 文件清洗。"""
        result = clean_content(b"key: value", "yml")
        assert "key" in result.text


class TestCleanCode:
    def test_python_code_preserved(self):
        """Python 代码基本保留。"""
        code = b"def hello():\n    return 'world'"
        result = clean_content(code, "py")
        assert "def hello" in result.text

    def test_typescript_code_preserved(self):
        """TypeScript 代码基本保留。"""
        code = b"const x: number = 1;"
        result = clean_content(code, "ts")
        assert "const x" in result.text

    def test_reduces_excess_blank_lines(self):
        """连续 5+ 空行缩减。"""
        code = b"line1\n\n\n\n\n\n\nline2"
        result = clean_content(code, "py")
        # 应缩减到最多 4 个连续空行
        assert "\n\n\n\n\n" not in result.text


class TestCleanCSV:
    def test_csv_with_header(self):
        """CSV 带表头结构化转换。"""
        csv_data = b"name,age\nAlice,30\nBob,25"
        result = clean_content(csv_data, "csv")
        assert "name: Alice" in result.text
        assert "age: 30" in result.text
        assert "name: Bob" in result.text

    def test_empty_csv(self):
        """空 CSV 返回警告。"""
        result = clean_content(b"", "csv")
        assert result.warnings
        assert not result.text.strip()

    def test_csv_with_empty_rows(self):
        """CSV 空行跳过。"""
        csv_data = b"name,age\nAlice,30\n,,\nBob,25"  
        result = clean_content(csv_data, "csv")
        assert "Alice" in result.text
        assert "Bob" in result.text


class TestCleanHTML:
    def test_removes_scripts(self):
        """HTML 去除 script 标签。"""
        html = b"<html><body><script>alert(1)</script><p>Hello</p></body></html>"
        result = clean_content(html, "html")
        assert "alert" not in result.text
        assert "Hello" in result.text

    def test_removes_styles(self):
        """HTML 去除 style 标签。"""
        html = b"<html><style>.x{color:red}</style><p>Hi</p></html>"
        result = clean_content(html, "html")
        assert ".x" not in result.text
        assert "Hi" in result.text


class TestUnsupportedType:
    def test_image_skipped(self):
        """图片文件跳过并返回警告。"""
        result = clean_content(b"fake-png-data", "png")
        assert result.warnings
        assert not result.text.strip()

    def test_binary_skipped(self):
        """二进制文件跳过。"""
        result = clean_content(b"fake-exe", "exe")
        assert result.warnings
        assert not result.text.strip()


class TestUnknownType:
    def test_falls_back_to_text(self):
        """未知类型回退纯文本清洗。"""
        result = clean_content("some random content".encode("utf-8"), "xyz")
        assert "some random content" in result.text