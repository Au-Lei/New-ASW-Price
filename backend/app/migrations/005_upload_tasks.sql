ALTER TABLE uploaded_files ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json));
ALTER TABLE extraction_tasks ADD COLUMN run_token TEXT;
ALTER TABLE extraction_tasks ADD COLUMN error_code TEXT;
CREATE UNIQUE INDEX extraction_one_task_per_file ON extraction_tasks(file_id);
CREATE TABLE extraction_attempts (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES extraction_tasks(id), attempt INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('processing','success','failed')),
 started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at TEXT,
 result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)), error_code TEXT, error TEXT,
 UNIQUE(task_id,attempt)
);
CREATE INDEX extraction_queue ON extraction_tasks(status,created_at);
CREATE TABLE excel_imports_multi (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES upload_batches(id),
 file_id TEXT NOT NULL UNIQUE REFERENCES uploaded_files(id), sha256 TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL DEFAULT 'preview' CHECK(status IN ('preview','committed')),
 options_json TEXT NOT NULL CHECK(json_valid(options_json)),
 preview_json TEXT NOT NULL CHECK(json_valid(preview_json)),
 selection_json TEXT CHECK(selection_json IS NULL OR json_valid(selection_json)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, committed_at TEXT
);
INSERT INTO excel_imports_multi SELECT * FROM excel_imports;
DROP TABLE excel_imports;
ALTER TABLE excel_imports_multi RENAME TO excel_imports;
CREATE INDEX excel_import_batch ON excel_imports(batch_id);
