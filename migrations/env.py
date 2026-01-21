from logging.config import fileConfig
import os

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from alembic import context
from dotenv import load_dotenv

# =========================
# .env を読み込む
# =========================
load_dotenv()

# Alembic Config オブジェクト
config = context.config

# .ini の logging 設定
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# =========================
# FastAPI 側の metadata を使う
# =========================
from app.db import Base
from app import models  # ← これ重要（modelsをimportしないとautogenerateされない）

target_metadata = Base.metadata

# =========================
# DB URL を .env から設定
# =========================
db_url = os.getenv("DB_URL")
if not db_url:
    raise RuntimeError("DB_URL is not set in .env")

config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,          # 型変更も検知
        compare_server_default=True # default変更も検知
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
