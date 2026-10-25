CREATE TABLE countries (
 id TEXT PRIMARY KEY, name_en TEXT NOT NULL, name_cn TEXT NOT NULL DEFAULT '', name_key TEXT NOT NULL UNIQUE,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE regions (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE routes (
 id TEXT PRIMARY KEY, region_id TEXT NOT NULL REFERENCES regions(id), name TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), revision INTEGER NOT NULL DEFAULT 1,
 UNIQUE(region_id,name)
);
CREATE TABLE ports (
 id TEXT PRIMARY KEY, country_id TEXT NOT NULL REFERENCES countries(id), name_en TEXT NOT NULL,
 name_cn TEXT NOT NULL DEFAULT '', name_key TEXT NOT NULL, terminal TEXT NOT NULL DEFAULT '', terminal_key TEXT NOT NULL DEFAULT '',
 port_code TEXT, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), revision INTEGER NOT NULL DEFAULT 1,
 UNIQUE(country_id,name_key,terminal_key)
);
CREATE TABLE port_routes (
 port_id TEXT NOT NULL REFERENCES ports(id), route_id TEXT NOT NULL REFERENCES routes(id), PRIMARY KEY(port_id,route_id)
);
CREATE TABLE port_aliases (
 id TEXT PRIMARY KEY, port_id TEXT NOT NULL REFERENCES ports(id), name TEXT NOT NULL, name_key TEXT NOT NULL,
 confirmed INTEGER NOT NULL CHECK(confirmed IN (0,1)), active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(port_id,name_key)
);
CREATE INDEX port_alias_search ON port_aliases(name_key);
CREATE TABLE carriers (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE carrier_aliases (
 id TEXT PRIMARY KEY, carrier_id TEXT NOT NULL REFERENCES carriers(id), name TEXT NOT NULL, name_key TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), revision INTEGER NOT NULL DEFAULT 1,
 UNIQUE(carrier_id,name_key)
);
CREATE TABLE suppliers (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE,
 kind TEXT NOT NULL CHECK(kind IN ('carrier','agent','other')),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), revision INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE source_series (
 id TEXT PRIMARY KEY, supplier_id TEXT NOT NULL REFERENCES suppliers(id), name TEXT NOT NULL, name_key TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), revision INTEGER NOT NULL DEFAULT 1,
 UNIQUE(supplier_id,name_key)
);
CREATE TABLE users (
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('business','admin')), active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
);
CREATE TABLE upload_batches (
 id TEXT PRIMARY KEY, owner_id TEXT REFERENCES users(id), status TEXT NOT NULL DEFAULT 'draft'
 CHECK(status IN ('draft','pending','approved','rejected','withdrawn')), revision INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE uploaded_files (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES upload_batches(id), original_name TEXT NOT NULL,
 storage_key TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL, media_type TEXT NOT NULL, size_bytes INTEGER NOT NULL CHECK(size_bytes>=0),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX uploaded_file_hash ON uploaded_files(sha256);
CREATE TABLE extraction_tasks (
 id TEXT PRIMARY KEY, file_id TEXT NOT NULL REFERENCES uploaded_files(id),
 status TEXT NOT NULL CHECK(status IN ('waiting','processing','success','failed')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0), raw_result_json TEXT CHECK(raw_result_json IS NULL OR json_valid(raw_result_json)),
 error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE quotes (
 id TEXT PRIMARY KEY, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 disabled_reason TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK(active=1 OR length(trim(coalesce(disabled_reason,'')))>0)
);
CREATE TABLE quote_versions (
 id TEXT PRIMARY KEY, quote_id TEXT NOT NULL REFERENCES quotes(id), version INTEGER NOT NULL CHECK(version>0),
 previous_version_id TEXT REFERENCES quote_versions(id), is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0,1)),
 origin_port_id TEXT NOT NULL REFERENCES ports(id), destination_port_id TEXT NOT NULL REFERENCES ports(id),
 route_id TEXT NOT NULL REFERENCES routes(id), carrier_id TEXT NOT NULL REFERENCES carriers(id),
 source_series_id TEXT NOT NULL REFERENCES source_series(id), service_key TEXT NOT NULL CHECK(length(trim(service_key))>0),
 service_kind TEXT NOT NULL CHECK(service_kind IN ('direct','transshipment')), path_text TEXT NOT NULL DEFAULT '',
 fee_basis TEXT NOT NULL CHECK(length(trim(fee_basis))>0), currency TEXT NOT NULL CHECK(length(currency)=3 AND currency GLOB '[A-Z][A-Z][A-Z]'),
 sailing TEXT NOT NULL DEFAULT '', transit_days INTEGER CHECK(transit_days>0), remark TEXT NOT NULL DEFAULT '',
 valid_from TEXT NOT NULL CHECK(length(valid_from)=10 AND date(valid_from,'+0 days') IS NOT NULL AND date(valid_from,'+0 days')=valid_from),
 valid_to TEXT NOT NULL CHECK(length(valid_to)=10 AND date(valid_to,'+0 days') IS NOT NULL AND date(valid_to,'+0 days')=valid_to),
 validity_basis TEXT NOT NULL CHECK(validity_basis='ETD'), validity_original TEXT NOT NULL DEFAULT '',
 included_fees TEXT NOT NULL DEFAULT '未说明', extra_fees TEXT NOT NULL DEFAULT '未说明', unknown_fees TEXT NOT NULL DEFAULT '未说明',
 conditions TEXT NOT NULL DEFAULT '', conditions_confirmed INTEGER NOT NULL CHECK(conditions_confirmed IN (0,1)),
 origin_confirmed INTEGER NOT NULL CHECK(origin_confirmed=1),
 created_by TEXT NOT NULL REFERENCES users(id), approved_by TEXT NOT NULL REFERENCES users(id),
 submitted_at TEXT NOT NULL, approved_at TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(quote_id,version), CHECK(valid_from<=valid_to), CHECK(conditions='' OR conditions_confirmed=1),
 FOREIGN KEY(destination_port_id,route_id) REFERENCES port_routes(port_id,route_id)
);
CREATE UNIQUE INDEX quote_current_version ON quote_versions(quote_id) WHERE is_current=1;
CREATE INDEX quote_query_ports_dates ON quote_versions(origin_port_id,destination_port_id,valid_from,valid_to);
CREATE TABLE quote_prices (
 version_id TEXT NOT NULL REFERENCES quote_versions(id), container_type TEXT NOT NULL CHECK(container_type IN ('20GP','40GP','40HQ')),
 amount_minor INTEGER NOT NULL CHECK(typeof(amount_minor)='integer' AND amount_minor>=0),
 zero_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(zero_confirmed IN (0,1)),
 PRIMARY KEY(version_id,container_type), CHECK(amount_minor>0 OR zero_confirmed=1)
);
CREATE TABLE draft_rows (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES upload_batches(id), task_id TEXT REFERENCES extraction_tasks(id),
 target_quote_id TEXT REFERENCES quotes(id), expected_version INTEGER,
 raw_json TEXT NOT NULL CHECK(json_valid(raw_json)), edited_json TEXT NOT NULL CHECK(json_valid(edited_json)),
 field_actions_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(field_actions_json)),
 validation_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(validation_json)),
 included INTEGER NOT NULL DEFAULT 1 CHECK(included IN (0,1)), revision INTEGER NOT NULL DEFAULT 1,
 updated_by TEXT REFERENCES users(id), updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE source_evidence (
 id TEXT PRIMARY KEY, file_id TEXT REFERENCES uploaded_files(id), task_id TEXT REFERENCES extraction_tasks(id),
 draft_row_id TEXT REFERENCES draft_rows(id), quote_version_id TEXT REFERENCES quote_versions(id),
 worksheet TEXT, cell_range TEXT, page_number INTEGER CHECK(page_number>0), image_region_json TEXT CHECK(image_region_json IS NULL OR json_valid(image_region_json)),
 manual_note TEXT, CHECK(file_id IS NOT NULL OR length(trim(coalesce(manual_note,'')))>0)
);
CREATE TABLE submissions (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES upload_batches(id), batch_revision INTEGER NOT NULL,
 snapshot_json TEXT NOT NULL CHECK(json_valid(snapshot_json)), submitted_by TEXT NOT NULL REFERENCES users(id),
 submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(batch_id,batch_revision)
);
CREATE TABLE approval_records (
 id TEXT PRIMARY KEY, submission_id TEXT NOT NULL UNIQUE REFERENCES submissions(id), operation_key TEXT NOT NULL UNIQUE,
 decision TEXT NOT NULL CHECK(decision IN ('approved','rejected')), reason TEXT NOT NULL DEFAULT '',
 actor_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK(decision!='rejected' OR length(trim(reason))>0)
);
CREATE TABLE audit_events (
 id TEXT PRIMARY KEY, actor_id TEXT REFERENCES users(id), actor_label TEXT NOT NULL,
 action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
 before_json TEXT CHECK(before_json IS NULL OR json_valid(before_json)), after_json TEXT CHECK(after_json IS NULL OR json_valid(after_json)),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE catalog_imports (
 id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, source_name TEXT NOT NULL,
 source_sha256 TEXT NOT NULL, rows_count INTEGER NOT NULL, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE catalog_evidence (
 import_id TEXT NOT NULL REFERENCES catalog_imports(id), port_id TEXT NOT NULL REFERENCES ports(id),
 route_id TEXT NOT NULL REFERENCES routes(id), worksheet TEXT NOT NULL, row_number INTEGER NOT NULL,
 original_json TEXT NOT NULL CHECK(json_valid(original_json)), PRIMARY KEY(import_id,worksheet,row_number)
);
