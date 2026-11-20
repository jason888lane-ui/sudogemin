-- Sudogemin Master schema and row-level tenant isolation.
-- Run as a dedicated schema/migration owner, never as the runtime app role.
CREATE TABLE IF NOT EXISTS data_sources (
 id BIGSERIAL PRIMARY KEY, tenant_id VARCHAR(160) NOT NULL, name VARCHAR(240) NOT NULL,
 source_uri TEXT NOT NULL, license_url TEXT NOT NULL, license_text TEXT NOT NULL,
 permission_basis VARCHAR(40) NOT NULL, evidence_hash VARCHAR(64) NOT NULL,
 verified_by VARCHAR(200), verified_at TIMESTAMPTZ, expires_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS datasets (
 id BIGSERIAL PRIMARY KEY, tenant_id VARCHAR(160) NOT NULL,
 source_id BIGINT NOT NULL REFERENCES data_sources(id), name VARCHAR(200) NOT NULL,
 records_json TEXT NOT NULL, record_count INTEGER NOT NULL, merkle_root VARCHAR(64) NOT NULL,
 status VARCHAR(40) NOT NULL DEFAULT 'pending_rights_review', created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_runs (
 id BIGSERIAL PRIMARY KEY, tenant_id VARCHAR(160) NOT NULL, agent_name VARCHAR(50) NOT NULL,
 model VARCHAR(100) NOT NULL, input_hash VARCHAR(64) NOT NULL, output_json TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS subscriptions (
 id BIGSERIAL PRIMARY KEY, tenant_id VARCHAR(160) NOT NULL UNIQUE,
 stripe_customer_id VARCHAR(120), stripe_subscription_id VARCHAR(120) UNIQUE,
 status VARCHAR(40) NOT NULL DEFAULT 'incomplete', current_period_end TIMESTAMPTZ,
 cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS stripe_events (
 id VARCHAR(120) PRIMARY KEY, event_type VARCHAR(120) NOT NULL,
 received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_data_sources_tenant ON data_sources(tenant_id);
CREATE INDEX IF NOT EXISTS ix_datasets_tenant ON datasets(tenant_id);
CREATE INDEX IF NOT EXISTS ix_agent_runs_tenant ON agent_runs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_subscriptions_tenant ON subscriptions(tenant_id);

DO $$
DECLARE t text;
BEGIN
 FOREACH t IN ARRAY ARRAY['data_sources','datasets','agent_runs','subscriptions'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('DROP POLICY IF EXISTS sudogemin_tenant_policy ON %I',t);
  EXECUTE format(
   'CREATE POLICY sudogemin_tenant_policy ON %I USING (tenant_id = NULLIF(current_setting(''app.tenant_id'',true), '''')) WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'',true), ''''))',
   t
  );
 END LOOP;
END $$;

-- Create a runtime role separately and grant only required DML permissions.
-- Runtime role MUST NOT own these tables, be SUPERUSER, or have BYPASSRLS.
-- Example:
-- REVOKE ALL ON data_sources,datasets,agent_runs,subscriptions,stripe_events FROM PUBLIC;
-- GRANT SELECT,INSERT,UPDATE,DELETE ON data_sources,datasets,agent_runs,subscriptions,stripe_events TO sudogemin_app;
-- GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO sudogemin_app;
