import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { AuthUser, LoginPayload, SignupPayload } from "@/types/auth";
import * as authApi from "@/api/auth";
import {
  clearSession,
  decodeAccessTokenClaims,
  dedupedRefresh,
  getRefreshToken,
  setAccessToken,
  setRefreshToken,
} from "./tokenStore";

interface AuthContextValue {
  user: AuthUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  signup: (payload: SignupPayload) => Promise<void>;
  login: (payload: LoginPayload) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const loadUserFromAccessToken = useCallback(async (accessToken: string) => {
    const claims = decodeAccessTokenClaims(accessToken);
    const organizationId = claims?.organization_id ?? "";
    const currentUser = await authApi.getCurrentUser(organizationId);
    setUser(currentUser);
  }, []);

  useEffect(() => {
    const storedRefreshToken = getRefreshToken();
    if (!storedRefreshToken) {
      setIsLoading(false);
      return;
    }
    dedupedRefresh(storedRefreshToken, authApi.refreshSession)
      .then(async (tokens) => {
        setAccessToken(tokens.accessToken);
        setRefreshToken(tokens.refreshToken);
        await loadUserFromAccessToken(tokens.accessToken);
      })
      .catch(() => {
        clearSession();
        setUser(null);
      })
      .finally(() => setIsLoading(false));
  }, [loadUserFromAccessToken]);

  const handleSignup = useCallback(
    async (payload: SignupPayload) => {
      const tokens = await authApi.signup(payload);
      setAccessToken(tokens.accessToken);
      setRefreshToken(tokens.refreshToken);
      await loadUserFromAccessToken(tokens.accessToken);
    },
    [loadUserFromAccessToken],
  );

  const handleLogin = useCallback(
    async (payload: LoginPayload) => {
      const tokens = await authApi.login(payload);
      setAccessToken(tokens.accessToken);
      setRefreshToken(tokens.refreshToken);
      await loadUserFromAccessToken(tokens.accessToken);
    },
    [loadUserFromAccessToken],
  );

  const handleLogout = useCallback(async () => {
    const storedRefreshToken = getRefreshToken();
    try {
      if (storedRefreshToken) {
        await authApi.logout(storedRefreshToken);
      }
    } finally {
      clearSession();
      setUser(null);
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      isAuthenticated: Boolean(user),
      isLoading,
      signup: handleSignup,
      login: handleLogin,
      logout: handleLogout,
    }),
    [user, isLoading, handleSignup, handleLogin, handleLogout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
