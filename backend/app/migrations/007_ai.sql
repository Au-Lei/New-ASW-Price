ALTER TABLE uploaded_files ADD COLUMN ai_consent_json TEXT CHECK(ai_consent_json IS NULL OR json_valid(ai_consent_json));
CREATE TABLE ai_calls (
 id TEXT PRIMARY KEY, file_id TEXT NOT NULL REFERENCES uploaded_files(id), attempt_id TEXT NOT NULL REFERENCES extraction_attempts(id),
 ordinal INTEGER NOT NULL, input_sha256 TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL,
 location_json TEXT NOT NULL CHECK(json_valid(location_json)),
 status TEXT NOT NULL CHECK(status IN ('processing','success','failed')), response_json TEXT CHECK(response_json IS NULL OR json_valid(response_json)),
 error_code TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at TEXT,
 UNIQUE(attempt_id,ordinal)
);
CREATE INDEX ai_call_cache ON ai_calls(file_id,input_sha256,model,prompt_version,status);
CREATE TABLE ai_imports (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES extraction_tasks(id), file_id TEXT NOT NULL REFERENCES uploaded_files(id),
 batch_id TEXT NOT NULL REFERENCES upload_batches(id), status TEXT NOT NULL DEFAULT 'preview' CHECK(status IN ('preview','committed')),
 preview_json TEXT NOT NULL CHECK(json_valid(preview_json)), selection_json TEXT CHECK(selection_json IS NULL OR json_valid(selection_json)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, committed_at TEXT
);
