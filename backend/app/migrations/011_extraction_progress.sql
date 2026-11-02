ALTER TABLE extraction_tasks ADD COLUMN progress_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(progress_json));
