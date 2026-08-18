-- ============================================================================
-- Bitbucket Integrations Schema (ticket a1cfabd7 — Bitbucket VCS support)
-- ============================================================================
-- Twin of `github_integrations`. Persists the per-user Bitbucket OAuth
-- provider token (from the Supabase provider=bitbucket flow) so the
-- /me/bitbucket/* endpoints can list repos and auto-route them into
-- workspace_repos (host = 'bitbucket.org').
--
-- The /me/bitbucket endpoints call Supabase with the USER's access token
-- (get_data_store(access_token=user_token)), NOT service_role — so these rows
-- are governed by user-scoped RLS (user_id = auth.uid()), exactly like the
-- user-scope half of route_rules. service_role (the backend's default key)
-- bypasses RLS either way.
--
-- SELF-HOST NOTE: this migration is only for the Supabase (cloud) path. The
-- default local doc-store is schemaless and needs nothing.
--
-- ⚠️ AUTHORED FROM THE CODE CONTRACT, not dumped from the live
-- github_integrations table (that DDL is not in-repo, and neither operator's
-- Supabase MCP is currently bound to yoru's project ozgnzurxuudzpsyrlnbj).
-- Before applying, reconcile TWO fields against the live github_integrations:
--   1. `scopes` column type — modeled here as JSONB (payload sends a JSON
--      list). If github_integrations uses text[], match that instead.
--   2. Upsert conflict target — connect_bitbucket does `.upsert(payload)` with
--      no `id`, keyed on user_id. That requires UNIQUE(user_id) (below) AND,
--      depending on how github's upsert is invoked, possibly an explicit
--      on_conflict=user_id in the client call. Mirror whatever github does.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.bitbucket_integrations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    provider_token TEXT NOT NULL,
    bitbucket_uuid TEXT,
    bitbucket_username TEXT,
    scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    connected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One Bitbucket connection per user — the connect flow upserts on user_id.
    CONSTRAINT bitbucket_integrations_user_unique UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS idx_bitbucket_integrations_user
    ON public.bitbucket_integrations(user_id);

-- Updated_at trigger
CREATE OR REPLACE FUNCTION update_bitbucket_integrations_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

ALTER FUNCTION public.update_bitbucket_integrations_updated_at()
    SET search_path = public, pg_temp;

DROP TRIGGER IF EXISTS bitbucket_integrations_updated_at ON public.bitbucket_integrations;
CREATE TRIGGER bitbucket_integrations_updated_at
    BEFORE UPDATE ON public.bitbucket_integrations
    FOR EACH ROW
    EXECUTE FUNCTION update_bitbucket_integrations_updated_at();

-- ============================================================================
-- RLS — a Bitbucket integration row is private to its owner. The token is a
-- credential; only the owning user (and service_role, which bypasses RLS) may
-- ever read or write it.
-- ============================================================================
ALTER TABLE public.bitbucket_integrations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS bitbucket_integrations_select ON public.bitbucket_integrations;
CREATE POLICY bitbucket_integrations_select ON public.bitbucket_integrations
    FOR SELECT
    USING (user_id = auth.uid());

DROP POLICY IF EXISTS bitbucket_integrations_insert ON public.bitbucket_integrations;
CREATE POLICY bitbucket_integrations_insert ON public.bitbucket_integrations
    FOR INSERT
    WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS bitbucket_integrations_update ON public.bitbucket_integrations;
CREATE POLICY bitbucket_integrations_update ON public.bitbucket_integrations
    FOR UPDATE
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS bitbucket_integrations_delete ON public.bitbucket_integrations;
CREATE POLICY bitbucket_integrations_delete ON public.bitbucket_integrations
    FOR DELETE
    USING (user_id = auth.uid());

COMMENT ON TABLE public.bitbucket_integrations IS
    'Per-user Bitbucket OAuth provider token (twin of github_integrations). Powers /me/bitbucket/* repo listing + auto-route. Owner-private via RLS.';
