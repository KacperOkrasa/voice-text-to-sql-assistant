"""Check durable history without executing SQL on restore."""

import unittest
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

from src.storage import (
    add_analysis, archive_database, create_conversation, decode_entry, delete_conversation,
    list_imported_databases, load_active_conversation, load_conversation, load_conversations, register_database,
    rename_database, save_active_conversation, save_analysis, save_conversation,
)


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        storage_patch = patch("src.storage.DEFAULT_STORAGE_PATH", Path(temporary.name) / "history.db")
        storage_patch.start()
        self.addCleanup(storage_patch.stop)

    def test_results_metadata_and_preferences_survive_roundtrip(self) -> None:
        conversation = create_conversation("custom_database")
        conversation["title"] = "Sprzedaż"
        save_conversation(conversation)
        frame = pd.DataFrame({
            "code": ["001", "002"], "value_pln": [2989.45, None],
            "id": [9223372036854775806, 9223372036854775807],
        })
        entry = {"sql": "SELECT ...", "title": "Sprzedaż", "frame": frame, "error": None}
        entry["id"] = add_analysis(conversation["id"], entry)
        entry["measure"] = "value_pln"
        save_analysis(entry)
        restored = load_conversations()[0]
        self.assertEqual(restored["database_id"], "custom_database")
        self.assertEqual(restored["title"], "Sprzedaż")
        self.assertEqual(restored["entries"][0]["measure"], "value_pln")
        assert_frame_equal(restored["entries"][0]["frame"], frame)

    def test_empty_duplicate_and_error_results_survive_roundtrip(self) -> None:
        conversation = create_conversation()
        frames = [
            pd.DataFrame(), pd.DataFrame(columns=["name", "value"]),
            pd.DataFrame([[1, 2]], columns=["quantity", "quantity"]),
        ]
        for frame in frames:
            add_analysis(conversation["id"], {"frame": frame, "error": "Błąd", "title": "Test"})
        restored = load_conversations()[0]["entries"]
        for entry, frame in zip(restored, frames):
            self.assertEqual(entry["error"], "Błąd")
            assert_frame_equal(entry["frame"], frame)

    def test_independent_appends_have_unique_ids_and_keep_both_results(self) -> None:
        first = create_conversation()
        second = create_conversation()
        entry = {"frame": pd.DataFrame({"value": [1]})}
        ids = [add_analysis(first["id"], entry), add_analysis(first["id"], entry)]
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(*ids)
        self.assertEqual(len(load_conversations()[0]["entries"]), 2)

    def test_refresh_loads_only_the_requested_conversation(self) -> None:
        first = create_conversation()
        other = create_conversation("custom")
        first_id = add_analysis(first["id"], {"frame": pd.DataFrame({"value": [42]})})
        add_analysis(other["id"], {"frame": pd.DataFrame({"value": [7]})})
        first["title"] = "Nowa nazwa"
        save_conversation(first)
        with patch("src.storage.decode_entry", wraps=decode_entry) as decode:
            refreshed = load_conversation(first["id"])
        self.assertEqual(decode.call_count, 1)
        self.assertEqual(refreshed["title"], "Nowa nazwa")
        self.assertEqual(refreshed["entries"][0]["id"], first_id)
        self.assertEqual(refreshed["entries"][0]["frame"].iloc[0, 0], 42)
        delete_conversation(first["id"])
        self.assertIsNone(load_conversation(first["id"]))
        self.assertIsNone(load_conversation(999))

    def test_deleting_conversation_removes_results_and_saved_selection(self) -> None:
        conversation = create_conversation()
        add_analysis(conversation["id"], {"frame": pd.DataFrame({"total": [42]})})
        other = create_conversation()
        add_analysis(other["id"], {"frame": pd.DataFrame({"total": [7]})})
        save_active_conversation(conversation["id"])
        self.assertEqual(load_active_conversation(), conversation["id"])
        delete_conversation(conversation["id"])
        self.assertIsNone(load_active_conversation())
        remaining = load_conversations()
        self.assertEqual([item["id"] for item in remaining], [other["id"]])
        self.assertEqual(remaining[0]["entries"][0]["frame"].iloc[0, 0], 7)
        from src.storage import DEFAULT_STORAGE_PATH
        with sqlite3.connect(DEFAULT_STORAGE_PATH) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM analyses WHERE conversation_id = ?",
                                                (conversation["id"],)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM conversations WHERE id = ?",
                                                (conversation["id"],)).fetchone()[0], 0)

    def test_deleting_another_conversation_preserves_the_saved_selection(self) -> None:
        first = create_conversation()
        second = create_conversation()
        save_active_conversation(first["id"])
        delete_conversation(second["id"])
        self.assertEqual(load_active_conversation(), first["id"])

    def test_database_names_dates_and_trash_are_durable(self) -> None:
        database = {"id": "one", "name": "source.db", "path": "/tmp/source.db"}
        register_database(database)
        imported_at = list_imported_databases()[0]["imported_at"]
        rename_database("one", "Sprzedaż")
        archive_database("one")
        self.assertEqual(list_imported_databases(), [])
        self.assertEqual(list_imported_databases(include_archived=True)[0]["name"], "Sprzedaż")
        register_database(database)  # Re-importing restores the same record and custom name.
        restored = list_imported_databases()[0]
        self.assertEqual(restored["name"], "Sprzedaż")
        self.assertEqual(restored["imported_at"], imported_at)
        for name in ("  ", "a" * 81):
            with self.assertRaises(ValueError):
                rename_database("one", name)

    def test_old_local_database_is_migrated_without_losing_history(self) -> None:
        from src.storage import DEFAULT_STORAGE_PATH
        with sqlite3.connect(DEFAULT_STORAGE_PATH) as connection:
            connection.executescript("""
                CREATE TABLE conversations (id INTEGER PRIMARY KEY, title TEXT, database_id TEXT);
                CREATE TABLE analyses (id INTEGER PRIMARY KEY, conversation_id INTEGER, payload TEXT);
                CREATE TABLE databases (id TEXT PRIMARY KEY, name TEXT, path TEXT);
                INSERT INTO conversations VALUES (1, 'Stara rozmowa', 'demo');
                INSERT INTO databases VALUES ('old', 'old.db', '/tmp/old.db');
            """)
        self.assertEqual(load_conversations()[0]["title"], "Stara rozmowa")
        self.assertNotIn("archived", load_conversations()[0])
        self.assertEqual(list_imported_databases()[0]["imported_at"], "")
        save_active_conversation(1)
        self.assertEqual(load_active_conversation(), 1)

    def test_corrupt_entry_does_not_hide_valid_entries_or_rewrite_payload(self):
        from src.storage import DEFAULT_STORAGE_PATH
        chat = create_conversation()
        add_analysis(chat["id"], {"frame": pd.DataFrame({"value": [42]})})
        payloads = ["{}", "not json", "[]", json.dumps({"format_version": 999}),
                    json.dumps({"frame": "{}", "dtypes": []})]
        with sqlite3.connect(DEFAULT_STORAGE_PATH) as connection:
            connection.executemany("INSERT INTO analyses (conversation_id, payload) VALUES (?, ?)",
                                   [(chat["id"], payload) for payload in payloads])
        for restored in (load_conversations()[0], load_conversation(chat["id"])):
            self.assertEqual(restored["entries"][0]["frame"].iloc[0, 0], 42)
            self.assertEqual(len(restored["entries"]), 6)
            for entry in restored["entries"][1:]:
                self.assertEqual(entry["source"], "corrupt")
                with self.assertRaises(ValueError):
                    save_analysis(entry)
        with sqlite3.connect(DEFAULT_STORAGE_PATH) as connection:
            self.assertEqual([row[0] for row in connection.execute("SELECT payload FROM analyses ORDER BY id").fetchall()][1:], payloads)

    def test_invalid_metadata_and_oversized_legacy_frame_are_isolated(self):
        from src.storage import encode_entry
        original = json.loads(encode_entry({"frame": pd.DataFrame({"value": [1]})}))
        for change in ({"api_usage": [None]}, {"elapsed": "invalid"}, {"previous_analysis": []},
                       {"frame": {"columns": ["x"], "data": [["x" * 70000]]}}):
            with self.subTest(change=list(change)):
                self.assertEqual(decode_entry(json.dumps(original | change), 1)["source"], "corrupt")



if __name__ == "__main__":
    unittest.main()
