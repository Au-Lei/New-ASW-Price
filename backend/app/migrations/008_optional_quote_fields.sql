CREATE TABLE quote_versions_optional (
 id TEXT PRIMARY KEY, quote_id TEXT NOT NULL REFERENCES quotes(id), version INTEGER NOT NULL CHECK(version>0),
 previous_version_id TEXT REFERENCES quote_versions(id), is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0,1)),
 origin_port_id TEXT REFERENCES ports(id), destination_port_id TEXT REFERENCES ports(id),
 route_id TEXT REFERENCES routes(id), carrier_id TEXT REFERENCES carriers(id),
 source_series_id TEXT REFERENCES source_series(id), service_key TEXT NOT NULL DEFAULT '',
 service_kind TEXT NOT NULL CHECK(service_kind IN ('','unknown','direct','transshipment')), path_text TEXT NOT NULL DEFAULT '',
 fee_basis TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL CHECK(currency='' OR (length(currency)=3 AND currency GLOB '[A-Z][A-Z][A-Z]')),
 sailing TEXT NOT NULL DEFAULT '', transit_days INTEGER CHECK(transit_days>0), remark TEXT NOT NULL DEFAULT '',
 valid_from TEXT NOT NULL CHECK(valid_from='' OR (length(valid_from)=10 AND date(valid_from,'+0 days') IS NOT NULL AND date(valid_from,'+0 days')=valid_from)),
 valid_to TEXT NOT NULL CHECK(valid_to='' OR (length(valid_to)=10 AND date(valid_to,'+0 days') IS NOT NULL AND date(valid_to,'+0 days')=valid_to)),
 validity_basis TEXT NOT NULL CHECK(validity_basis IN ('','unknown','ETD','other')), validity_original TEXT NOT NULL DEFAULT '',
 included_fees TEXT NOT NULL DEFAULT '未说明', extra_fees TEXT NOT NULL DEFAULT '未说明', unknown_fees TEXT NOT NULL DEFAULT '未说明',
 conditions TEXT NOT NULL DEFAULT '', conditions_confirmed INTEGER NOT NULL CHECK(conditions_confirmed IN (0,1)),
 origin_confirmed INTEGER NOT NULL CHECK(origin_confirmed IN (0,1)),
 created_by TEXT NOT NULL REFERENCES users(id), approved_by TEXT NOT NULL REFERENCES users(id),
 submitted_at TEXT NOT NULL, approved_at TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 display_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(display_json)),
 UNIQUE(quote_id,version), CHECK(valid_from='' OR valid_to='' OR valid_from<=valid_to),
 FOREIGN KEY(destination_port_id,route_id) REFERENCES port_routes(port_id,route_id)
);
INSERT INTO quote_versions_optional SELECT * FROM quote_versions;
DROP TABLE quote_versions;
ALTER TABLE quote_versions_optional RENAME TO quote_versions;
CREATE UNIQUE INDEX quote_current_version ON quote_versions(quote_id) WHERE is_current=1;
CREATE INDEX quote_query_ports_dates ON quote_versions(origin_port_id,destination_port_id,valid_from,valid_to);
