"""向量存储集成测试——需要真实 PostgreSQL + pgvector。"""

import uuid

import pytest
from agenthub.rag.knowledge.vector_store import VectorStore


@pytest.mark.asyncio
class TestVectorStore:
    async def test_upsert_and_list(self, db_session):
        """写入分块后文件列表可见。"""
        store = VectorStore(db_session)
        project_id = uuid.uuid4()
        file_id = f"test-file-{uuid.uuid4().hex[:8]}"
        chunks = [
            ("内容A", f"hash-a-{uuid.uuid4().hex[:8]}", {"line": 1}, None),
            ("内容B", f"hash-b-{uuid.uuid4().hex[:8]}", {"line": 5}, None),
        ]
        inserted = await store.upsert_chunks(
            project_id, file_id, "test.py", "py", chunks,
        )
        assert inserted == 2

        files = await store.list_files(project_id)
        assert any(f["file_id"] == file_id for f in files)

    async def test_deduplication(self, db_session):
        """相同 content_hash 不重复写入。"""
        store = VectorStore(db_session)
        project_id = uuid.uuid4()
        file_id = f"dedup-{uuid.uuid4().hex[:8]}"
        content_hash = f"hash-{uuid.uuid4().hex[:8]}"
        chunks = [("内容", content_hash, None, None)]
        # 第一次写入
        inserted1 = await store.upsert_chunks(project_id, file_id, "f.py", "py", chunks)
        assert inserted1 == 1
        # 第二次写入——去重
        inserted2 = await store.upsert_chunks(project_id, file_id, "f.py", "py", chunks)
        assert inserted2 == 0

    async def test_delete_by_file_id(self, db_session):
        """删除文件级联清除所有分块。"""
        store = VectorStore(db_session)
        project_id = uuid.uuid4()
        file_id = f"del-{uuid.uuid4().hex[:8]}"
        chunks = [
            (f"内容{i}", f"hash-del-{i}-{uuid.uuid4().hex[:8]}", None, None)
            for i in range(3)
        ]
        await store.upsert_chunks(project_id, file_id, "f.py", "py", chunks)
        # 删除
        deleted = await store.delete_by_file_id(project_id, file_id)
        assert deleted == 3
        # 验证已删除
        files = await store.list_files(project_id)
        assert not any(f["file_id"] == file_id for f in files)

    async def test_project_isolation(self, db_session):
        """不同项目的文件互不可见。"""
        store = VectorStore(db_session)
        pid1 = uuid.uuid4()
        pid2 = uuid.uuid4()
        fid1 = f"iso1-{uuid.uuid4().hex[:8]}"
        fid2 = f"iso2-{uuid.uuid4().hex[:8]}"

        await store.upsert_chunks(pid1, fid1, "a.py", "py", [("A", f"hash-iso-a", None, None)])
        await store.upsert_chunks(pid2, fid2, "b.py", "py", [("B", f"hash-iso-b", None, None)])

        files1 = await store.list_files(pid1)
        files2 = await store.list_files(pid2)
        assert any(f["file_id"] == fid1 for f in files1)
        assert not any(f["file_id"] == fid2 for f in files1)
        assert any(f["file_id"] == fid2 for f in files2)
        assert not any(f["file_id"] == fid1 for f in files2)

    async def test_hybrid_search_trgm(self, db_session):
        """关键词搜索（pg_trgm）返回结果。"""
        store = VectorStore(db_session)
        project_id = uuid.uuid4()
        file_id = f"search-{uuid.uuid4().hex[:8]}"
        chunks = [
            ("Python FastAPI 异步路由实现", f"hash-s1-{uuid.uuid4().hex[:8]}", None, None),
            ("美味蛋糕食谱", f"hash-s2-{uuid.uuid4().hex[:8]}", None, None),
        ]
        await store.upsert_chunks(project_id, file_id, "notes.md", "md", chunks)

        results = await store.hybrid_search(
            project_id, None, "FastAPI 路由", top_k=5,
        )
        assert len(results) > 0
        assert any("FastAPI" in r.content for r in results)