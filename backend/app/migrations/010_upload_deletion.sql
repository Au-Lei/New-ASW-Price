ALTER TABLE upload_batches ADD COLUMN deleted_at TEXT;
ALTER TABLE uploaded_files ADD COLUMN deleted_at TEXT;
-- Keep historical imports immutable while allowing a deleted file to be uploaded again.
CREATE TABLE excel_imports_retained (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES upload_batches(id),
 file_id TEXT NOT NULL UNIQUE REFERENCES uploaded_files(id), sha256 TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'preview' CHECK(status IN ('preview','committed')),
 options_json TEXT NOT NULL CHECK(json_valid(options_json)),
 preview_json TEXT NOT NULL CHECK(json_valid(preview_json)),
 selection_json TEXT CHECK(selection_json IS NULL OR json_valid(selection_json)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, committed_at TEXT
);
INSERT INTO excel_imports_retained SELECT * FROM excel_imports;
DROP TABLE excel_imports;
ALTER TABLE excel_imports_retained RENAME TO excel_imports;
CREATE INDEX excel_import_batch ON excel_imports(batch_id);
CREATE INDEX excel_import_sha ON excel_imports(sha256);
