import { apiRequest, mockDelay } from "./client";
import { USE_MOCK_DATA } from "./config";
import type { AuthUser, LoginPayload, SessionTokens, SignupPayload } from "@/types/auth";

interface UserProfileResponse {
  id: string;
  email: string;
  displayName: string;
  isActive: boolean;
  roles: string[];
  permissions: string[];
}

const MOCK_USER: AuthUser = {
  id: "user-5",
  name: "Bhawna Relhan",
  email: "bhawna.relhan@navikenz.com",
  organizationId: "org-1",
  role: "owner",
};

const MOCK_TOKENS: SessionTokens = {
  accessToken: "mock-access-token",
  refreshToken: "mock-refresh-token",
  tokenType: "bearer",
  expiresIn: 3600,
};

export async function signup(payload: SignupPayload): Promise<SessionTokens> {
  if (USE_MOCK_DATA) {
    return mockDelay(MOCK_TOKENS, 400);
  }
  return apiRequest<SessionTokens>("/auth/signup", { method: "POST", body: payload });
}

export async function login(payload: LoginPayload): Promise<SessionTokens> {
  if (USE_MOCK_DATA) {
    return mockDelay(MOCK_TOKENS, 400);
  }
  return apiRequest<SessionTokens>("/auth/login", { method: "POST", body: payload });
}

export async function refreshSession(refreshToken: string): Promise<SessionTokens> {
  return apiRequest<SessionTokens>("/auth/refresh", {
    method: "POST",
    body: { refreshToken },
  });
}

/**
 * `organizationId` comes from the caller (decoded off the access token's own
 * claims, `tokenStore.decodeAccessTokenClaims`) -- `GET /auth/me` describes
 * the user, not which organization their current session is scoped to.
 */
export async function getCurrentUser(organizationId: string): Promise<AuthUser> {
  if (USE_MOCK_DATA) {
    return mockDelay(MOCK_USER, 150);
  }
  const profile = await apiRequest<UserProfileResponse>("/auth/me");
  return {
    id: profile.id,
    name: profile.displayName,
    email: profile.email,
    organizationId,
    role: (profile.roles[0] as AuthUser["role"]) ?? "member",
  };
}

export async function logout(refreshToken: string): Promise<void> {
  if (USE_MOCK_DATA) {
    return mockDelay(undefined, 100);
  }
  return apiRequest<void>("/auth/logout", { method: "POST", body: { refreshToken } });
}
