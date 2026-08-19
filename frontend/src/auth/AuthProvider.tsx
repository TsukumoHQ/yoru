import { createContext, useCallback, useEffect, useState, type ReactNode } from "react"
import { getMe, signout as apiSignout, type AuthUser } from "../lib/auth-api"
import { setSelectedOrgId } from "../lib/org-scope"

interface AuthCtx {
  user: AuthUser | null
  loading: boolean
  refresh: () => Promise<void>
  signOut: () => Promise<void>
}

export const AuthContext = createContext<AuthCtx>({
  user: null,
  loading: true,
  refresh: async () => {},
  signOut: async () => {},
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    const me = await getMe()
    setUser(me)
    setLoading(false)
  }, [])

  useEffect(() => {
    let mounted = true
    getMe()
      .then((me) => {
        if (!mounted) return
        setUser(me)
      })
      .catch(() => {
        if (!mounted) return
        setUser(null)
      })
      .finally(() => {
        if (!mounted) return
        setLoading(false)
      })
    return () => {
      mounted = false
    }
  }, [])

  const signOut = useCallback(async () => {
    try {
      await apiSignout()
    } catch {
      // ignore — cookies will be cleared regardless once browser refreshes
    }
    setUser(null)
    // Clear the studio super-admin's org selection so a stale
    // `X-Organization-Id` (persisted in localStorage) can't leak into the NEXT
    // user's audit reads on a shared browser — a non-super-admin can't see or
    // clear the switcher, and a foreign org id wedges every read to a 404.
    setSelectedOrgId(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, refresh, signOut }}>
      {children}
    </AuthContext.Provider>
  )
}
