import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, call, getToken, onUnauthorized, setToken, type Schemas } from "../api/client";

type Role = Schemas["UserRole"];
const RANK: Record<Role, number> = { VIEWER: 1, EDITOR: 2, OWNER: 3 };

interface AuthState {
  me: Schemas["MeRead"] | null;
  loading: boolean;
  expired: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  /** Presentation hint only; the API enforces roles on every request. */
  can: (role: Role) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [me, setMe] = useState<Schemas["MeRead"] | null>(null);
  const [loading, setLoading] = useState<boolean>(Boolean(getToken()));
  const [expired, setExpired] = useState(false);

  const reset = useCallback(
    (wasExpired: boolean) => {
      setToken(null);
      setMe(null);
      setExpired(wasExpired);
      queryClient.clear();
    },
    [queryClient],
  );

  useEffect(() => onUnauthorized(() => reset(true)), [reset]);

  useEffect(() => {
    if (!getToken()) return;
    call(api.GET("/api/v1/auth/me"))
      .then((m) => setMe(m))
      .catch(() => reset(true))
      .finally(() => setLoading(false));
  }, [reset]);

  const signIn = useCallback(async (email: string, password: string) => {
    const tokenRead = await call(api.POST("/api/v1/auth/login", { body: { email, password } }));
    setToken(tokenRead.access_token);
    setExpired(false);
    setMe(await call(api.GET("/api/v1/auth/me")));
  }, []);

  const signOut = useCallback(async () => {
    try {
      await call(api.POST("/api/v1/auth/logout"));
    } finally {
      reset(false);
    }
  }, [reset]);

  const can = useCallback((role: Role) => (me ? RANK[me.role] >= RANK[role] : false), [me]);

  const value = useMemo(() => ({ me, loading, expired, signIn, signOut, can }), [me, loading, expired, signIn, signOut, can]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
