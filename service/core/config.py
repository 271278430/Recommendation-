"""配置：从环境变量读取，不硬编码路径/端口。

企业实践：12-factor——配置走 env，代码里不写死部署相关的值（路径、端口、DB）。
mastery_store 自己读 mastery_store/.env 连 PG，本服务不直连 DB，所以这里只配
服务自身需要的：项目根（定位题库/映射缓存）、监听端口、日志级别。
"""
import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 项目根（用于定位题库与 kp_id 映射缓存）；与 mastery_store.PROJECT_ROOT 同源
    project_root: str = os.environ.get("PROJECT_ROOT", "/data/shanghui/Recommend_question")
    app_host: str = "127.0.0.1"
    app_port: int = 8800
    log_level: str = "INFO"

    @property
    def qbank_path(self) -> str:
        return os.path.join(self.project_root, "data", "初中_九年级.jsonl")

    @property
    def kp_id_map_path(self) -> str:
        return os.path.join(self.project_root, "data", "kg_graph", "kp_id_map.json")


settings = Settings()
