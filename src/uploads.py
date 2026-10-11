"""Validate and keep local copies of uploaded SQLite databases."""

import hashlib
import sqlite3
from pathlib import Path
from tempfile import NamedTemporaryFile

from src.db import PROJECT_ROOT, validate_database
from src.storage import list_imported_databases, register_database

UPLOAD_DIRECTORY = PROJECT_ROOT / "data" / "uploads"
MAX_DATABASE_BYTES = 50 * 1024 * 1024


def import_database(name: str, content: bytes) -> dict:
    """Check a SQLite file before registering a copy; never execute SQL scripts."""
    if not content.startswith(b"SQLite format 3\x00"):
        raise ValueError("Plik nie jest bazą SQLite. Wybierz plik .db, .sqlite lub .sqlite3.")
    if len(content) > MAX_DATABASE_BYTES:
        raise ValueError("Baza może mieć maksymalnie 50 MB.")
    database_id = hashlib.sha256(content).hexdigest()
    UPLOAD_DIRECTORY.mkdir(parents=True, exist_ok=True)
    destination = UPLOAD_DIRECTORY / f"{database_id}.db"
    temporary = None
    try:
        with NamedTemporaryFile(dir=UPLOAD_DIRECTORY, suffix=".db", delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        validate_database(temporary)
        if not destination.exists():
            temporary.replace(destination)
        database = {"id": database_id, "name": Path(name).name, "path": str(destination)}
        register_database(database)
        return next(item for item in list_imported_databases() if item["id"] == database_id)
    except sqlite3.Error as error:
        raise ValueError("Baza jest uszkodzona, zawiera nieprawidłowe widoki lub przekracza limity odczytu.") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
