-- One-time setup: restricted RLS-enforced role for API/worker runtime.
--
-- IMPORTANT: alembic/versions/001_initial_schema.py already creates the
-- `humetric_app` role automatically (idempotent, on first `alembic upgrade
-- head`) with a hardcoded default password: 'humetric_app'. If migrations
-- have already run against this database (true for the production DB per
-- the beta plan), the role already exists with that weak default password.
-- This script does NOT try to CREATE the role (that would fail with
-- "role already exists") -- it only rotates the password and re-asserts
-- the grants, which is safe to run whether or not the role pre-exists.
--
-- Usage (psql needs a plain postgresql:// URL, not SQLAlchemy's
-- postgresql+psycopg:// form):
--   psql "postgresql://<admin_user>:<admin_password>@<host>:<port>/humetric" \
--     -f scripts/create_app_role.sql
-- Replace <STRONG_PASSWORD> before running (use [A-Za-z0-9_-] only — the
-- value is embedded in DATABASE_URL_APP without URL-encoding), then set the
-- same value as APP_DB_PASSWORD in the Dokploy compose env.

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'humetric_app') THEN
    CREATE ROLE humetric_app WITH LOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

ALTER ROLE humetric_app WITH LOGIN PASSWORD '<STRONG_PASSWORD>' NOSUPERUSER NOBYPASSRLS;

GRANT CONNECT ON DATABASE humetric TO humetric_app;
GRANT USAGE ON SCHEMA public TO humetric_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO humetric_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO humetric_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO humetric_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO humetric_app;
