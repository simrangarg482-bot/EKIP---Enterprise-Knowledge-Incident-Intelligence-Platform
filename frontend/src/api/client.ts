import type { ApiError } from "@/types/common";
import { API_BASE_URL } from "./config";
import { getAccessToken } from "@/context/tokenStore";
import { keysToCamelCase, keysToSnakeCase } from "./caseConversion";

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
}

/**
 * Thin fetch wrapper for the EKIP FastAPI backend. All real (non-mock)
 * resource modules in src/api/* route through this function so that auth
 * headers, base URL resolution, and error shaping stay in one place.
 */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const token = getAccessToken();

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: options.method ?? "GET",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: options.body !== undefined ? JSON.stringify(keysToSnakeCase(options.body)) : undefined,
    signal: options.signal,
  });

  if (!response.ok) {
    const error: ApiError = {
      status: response.status,
      message: response.statusText || "Request failed",
    };
    try {
      const payload = await response.json();
      error.detail = payload?.detail ?? payload?.message;
    } catch {
      // response had no JSON body
    }
    throw error;
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return keysToCamelCase<T>(await response.json());
}

/** Simulates network latency for the mock data layer so loading states are visible. */
export function mockDelay<T>(value: T, ms = 350): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}
