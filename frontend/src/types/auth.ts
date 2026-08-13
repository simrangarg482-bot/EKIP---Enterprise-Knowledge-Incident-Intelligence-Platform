export interface AuthUser {
  id: string;
  name: string;
  email: string;
  avatarUrl?: string;
  organizationId: string;
  role: "owner" | "admin" | "member" | "viewer";
}

export interface SessionTokens {
  accessToken: string;
  refreshToken: string;
  tokenType: "bearer";
  expiresIn: number;
}

export interface SignupPayload {
  email: string;
  password: string;
  displayName: string;
  organizationName: string;
  organizationSlug: string;
}

export interface LoginPayload {
  email: string;
  password: string;
}
