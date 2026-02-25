import os
import tempfile
import traceback

from raganything import RAGAnything, RAGAnythingConfig

from src.knowledge.base import FileStatus
from src.knowledge.implementations.lightrag import LightRagKB
from src.utils import logger
from src.utils.datetime_utils import utc_isoformat


class RagAnythingKB(LightRagKB):
    """基于 RagAnything 的知识库实现，支持多模态文档处理（图片、表格、公式等）

    在 LightRagKB 的基础上集成了 RagAnything，能够直接处理原始文件中的
    多模态内容，包括图片、表格和数学公式，无需预先将文件解析为 Markdown。
    """

    def __init__(self, work_dir: str, **kwargs):
        super().__init__(work_dir, **kwargs)
        # RagAnything instances indexed by db_id
        self.rag_anything_instances: dict[str, RAGAnything] = {}
        logger.info("RagAnythingKB initialized")

    @property
    def kb_type(self) -> str:
        """知识库类型标识"""
        return "raganything"

    async def _get_rag_anything_instance(self, db_id: str) -> RAGAnything | None:
        """获取或创建指定 db_id 的 RAGAnything 实例"""
        if db_id in self.rag_anything_instances:
            return self.rag_anything_instances[db_id]

        if db_id not in self.databases_meta:
            return None

        try:
            # 创建并初始化底层 LightRAG 实例
            rag = await self._create_kb_instance(db_id, {})
            await self._initialize_kb_instance(rag)
            self.instances[db_id] = rag

            # 准备 RAGAnything 输出目录
            working_dir = os.path.join(self.work_dir, db_id)
            output_dir = os.path.join(working_dir, "raganything_output")
            os.makedirs(output_dir, exist_ok=True)

            # 获取 LLM 函数（同时用于文本和视觉处理）
            llm_info = self.databases_meta[db_id].get("llm_info", {})
            llm_func = self._get_llm_func(llm_info)

            # 创建 RAGAnythingConfig，使用 docling 解析器（已在项目中安装）
            rag_config = RAGAnythingConfig(
                working_dir=working_dir,
                parser="docling",
                parser_output_dir=output_dir,
                enable_image_processing=True,
                enable_table_processing=True,
                enable_equation_processing=True,
                display_content_stats=False,
            )

            # 创建 RAGAnything 实例，传入已初始化的 LightRAG 实例
            rag_anything = RAGAnything(
                lightrag=rag,
                llm_model_func=llm_func,
                vision_model_func=llm_func,
                config=rag_config,
            )

            self.rag_anything_instances[db_id] = rag_anything
            logger.info(f"RAGAnything instance created for {db_id}")
            return rag_anything

        except Exception as e:
            logger.error(f"Failed to create RAGAnything instance for {db_id}: {e}")
            logger.error(traceback.format_exc())
            return None

    async def _download_file_to_temp(self, file_path: str, filename: str) -> str:
        """将 MinIO 中的文件下载到临时目录，返回临时文件路径"""
        from src.knowledge.utils.kb_utils import is_minio_url, parse_minio_url
        from src.storage.minio import get_minio_client

        if is_minio_url(file_path):
            bucket_name, object_name = parse_minio_url(file_path)
            minio_client = get_minio_client()
            file_bytes = await minio_client.adownload_file(bucket_name, object_name)

            suffix = os.path.splitext(filename)[1] or ".bin"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(file_bytes)
                return tmp.name
        else:
            # 本地文件直接返回路径
            return file_path

    async def index_file(self, db_id: str, file_id: str, operator_id: str | None = None) -> dict:
        """
        使用 RagAnything 对文件进行多模态索引
        (Status: UPLOADED/PARSED -> INDEXING -> INDEXED/ERROR_INDEXING)

        与 LightRagKB 不同，此方法直接处理原始文件，无需预先解析为 Markdown，
        支持对图片、表格、公式等多模态内容进行专项处理。

        Args:
            db_id: 数据库 ID
            file_id: 文件 ID
            operator_id: 操作用户 ID

        Returns:
            更新后的文件元数据
        """
        if db_id not in self.databases_meta:
            raise ValueError(f"Database {db_id} not found")

        rag_anything = await self._get_rag_anything_instance(db_id)
        if not rag_anything:
            raise ValueError(f"Failed to get RAGAnything instance for {db_id}")

        if file_id not in self.files_meta:
            raise ValueError(f"File {file_id} not found")

        file_meta = self.files_meta[file_id]

        # RagAnything 可以直接处理原始文件，因此允许从 UPLOADED 状态开始索引
        current_status = file_meta.get("status")
        allowed_statuses = {
            FileStatus.UPLOADED,
            FileStatus.PARSED,
            FileStatus.ERROR_INDEXING,
            FileStatus.INDEXED,
            "done",
        }

        if current_status not in allowed_statuses:
            raise ValueError(
                f"Cannot index file with status '{current_status}'. "
                f"File must be in one of: {', '.join(str(s) for s in allowed_statuses)}"
            )

        file_path = file_meta.get("path")
        if not file_path:
            raise ValueError(f"File {file_id} has no path in metadata")

        # 清除之前的错误信息
        if "error" in file_meta:
            self.files_meta[file_id].pop("error", None)

        # 更新状态为 INDEXING
        self.files_meta[file_id]["status"] = FileStatus.INDEXING
        self.files_meta[file_id]["updated_at"] = utc_isoformat()
        if operator_id:
            self.files_meta[file_id]["updated_by"] = operator_id
        self._save_metadata()

        self._add_to_processing_queue(file_id)

        tmp_file_path = None
        try:
            filename = file_meta.get("filename", file_id)

            # 将原始文件从 MinIO 下载到临时目录
            tmp_file_path = await self._download_file_to_temp(file_path, filename)

            # 删除已有的 chunks（用于重新索引）
            await self.delete_file_chunks_only(db_id, file_id)

            # 使用 RagAnything 处理文档（包含多模态内容）
            await rag_anything.process_document_complete(
                file_path=tmp_file_path,
                doc_id=file_id,
                file_name=filename,
            )

            logger.info(f"Indexed file {file_id} using RagAnything")

            # 更新状态为 INDEXED
            self.files_meta[file_id]["status"] = FileStatus.INDEXED
            self.files_meta[file_id]["updated_at"] = utc_isoformat()
            if operator_id:
                self.files_meta[file_id]["updated_by"] = operator_id
            self._save_metadata()

            return self.files_meta[file_id]

        except Exception as e:
            logger.error(f"RagAnything indexing failed for {file_id}: {e}")
            logger.error(traceback.format_exc())
            self.files_meta[file_id]["status"] = FileStatus.ERROR_INDEXING
            self.files_meta[file_id]["error"] = str(e)
            self.files_meta[file_id]["updated_at"] = utc_isoformat()
            if operator_id:
                self.files_meta[file_id]["updated_by"] = operator_id
            self._save_metadata()
            raise

        finally:
            self._remove_from_processing_queue(file_id)
            # 清理临时文件
            if tmp_file_path and os.path.exists(tmp_file_path):
                try:
                    os.unlink(tmp_file_path)
                except Exception:
                    pass

    def get_query_params_config(self, db_id: str, **kwargs) -> dict:
        """获取 RagAnything 知识库的查询参数配置（与 LightRAG 相同）"""
        params_config = super().get_query_params_config(db_id, **kwargs)
        params_config["type"] = "raganything"
        return params_config
