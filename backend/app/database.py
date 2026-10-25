import sqlite3
from contextlib import contextmanager
from pathlib import Path


MIGRATIONS = [
    (1, 'CREATE TABLE application_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)'),
    (2, (Path(__file__).parent / 'migrations/002_domain.sql').read_text()),
    (3, (Path(__file__).parent / 'migrations/003_excel.sql').read_text()),
    (4, (Path(__file__).parent / 'migrations/004_review.sql').read_text()),
    (5, (Path(__file__).parent / 'migrations/005_upload_tasks.sql').read_text()),
    (6, (Path(__file__).parent / 'migrations/006_accounts.sql').read_text()),
    (7, (Path(__file__).parent / 'migrations/007_ai.sql').read_text()),
    (8, (Path(__file__).parent / 'migrations/008_optional_quote_fields.sql').read_text()),
    (9, (Path(__file__).parent / 'migrations/009_port_metadata.sql').read_text()),
]


@contextmanager
def connect(path: Path):
    db = sqlite3.connect(path, timeout=10)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        with db:
            yield db
    finally:
        db.close()


def initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
        # Table rebuild keeps dependent evidence/prices and checks all foreign keys before commit.
        db.execute('PRAGMA foreign_keys=OFF')
        db.execute('BEGIN IMMEDIATE')
        current = {row[0] for row in db.execute('SELECT version FROM schema_migrations')}
        if current - {version for version, _ in MIGRATIONS}:
            raise RuntimeError('数据库版本高于当前程序，请使用匹配版本的程序')
        for version, sql in MIGRATIONS:
            if version not in current:
                statement = ''
                for character in sql:
                    statement += character
                    if sqlite3.complete_statement(statement):
                        db.execute(statement)
                        statement = ''
                if statement.strip():
                    db.execute(statement)
                db.execute('INSERT INTO schema_migrations(version) VALUES (?)', (version,))
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('数据库迁移外键校验失败')
        db.execute("INSERT OR IGNORE INTO application_meta(key,value) VALUES ('installation_id', lower(hex(randomblob(16))))")
        from .catalog_import import seed_catalog
        seed_catalog(db)
