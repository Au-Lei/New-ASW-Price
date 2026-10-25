INSERT INTO users(id,username,password_hash,role,active) VALUES ('local-admin','本机管理员','!local-only-no-login','admin',1);
CREATE TRIGGER submission_no_update BEFORE UPDATE ON submissions BEGIN SELECT RAISE(ABORT,'提交快照不可修改'); END;
CREATE TRIGGER submission_no_delete BEFORE DELETE ON submissions BEGIN SELECT RAISE(ABORT,'提交快照不可删除'); END;
CREATE TRIGGER approval_no_update BEFORE UPDATE ON approval_records BEGIN SELECT RAISE(ABORT,'审批记录不可修改'); END;
CREATE TRIGGER approval_no_delete BEFORE DELETE ON approval_records BEGIN SELECT RAISE(ABORT,'审批记录不可删除'); END;
ALTER TABLE quote_versions ADD COLUMN display_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(display_json));
