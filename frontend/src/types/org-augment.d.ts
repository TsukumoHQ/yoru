// Multi-tenant: the backend (design 44a3774a, M3) now stamps every session with
// its owning `org_id` and returns it on the list rows + detail. We surface it as
// an org badge in the studio super-admin cross-org view.
//
// Declared as a module augmentation rather than an edit to `@receipt/ui`'s
// `Session` type: the shared package is consumed as source across git worktrees,
// and a field added there is not visible to a worktree's own build. Augmenting
// the module here adds `org_id` to `Session` (and `SessionDetail`, which extends
// it) within the frontend's compilation only. Optional/nullable → soft-fallback:
// a single-org or pre-M3 backend simply omits it and no badge renders.
import "@receipt/ui"

declare module "@receipt/ui" {
  interface Session {
    org_id?: string | null
  }
}
