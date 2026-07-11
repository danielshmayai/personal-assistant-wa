-- Product layer: tenancy, sessions, secrets, modules.
-- Engine tables (memory, checkpoints, …) are managed by the engine itself.

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    avatar_url TEXT NOT NULL DEFAULT '',
    is_owner BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'active',  -- active | revoked | deleted
    onboarded_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tenant_google_identity (
    google_sub TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,                     -- jti
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS sessions_tenant_idx ON sessions(tenant_id);

-- Envelope encryption: one DEK per tenant, wrapped by the master KEK.
-- key_version tracks which KEK wrapped the DEK, so rotating the KEK only
-- means re-wrapping DEKs — never re-encrypting secret values.
CREATE TABLE IF NOT EXISTS tenant_deks (
    tenant_id TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    dek_wrapped TEXT NOT NULL,
    key_version INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tenant_secrets (
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (tenant_id, key)
);

CREATE TABLE IF NOT EXISTS tenant_modules (
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    module_id TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (tenant_id, module_id)
);
