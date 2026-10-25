CREATE TABLE port_metadata_cache (
 name_key TEXT PRIMARY KEY, input_name TEXT NOT NULL,
 metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 provider TEXT NOT NULL, model TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
