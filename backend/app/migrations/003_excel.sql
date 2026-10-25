CREATE TABLE excel_imports (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL UNIQUE REFERENCES upload_batches(id),
 file_id TEXT NOT NULL UNIQUE REFERENCES uploaded_files(id), sha256 TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL DEFAULT 'preview' CHECK(status IN ('preview','committed')),
 options_json TEXT NOT NULL CHECK(json_valid(options_json)),
 preview_json TEXT NOT NULL CHECK(json_valid(preview_json)),
 selection_json TEXT CHECK(selection_json IS NULL OR json_valid(selection_json)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, committed_at TEXT
);
