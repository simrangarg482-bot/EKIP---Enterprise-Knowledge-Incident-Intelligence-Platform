/**
 * The real backend has no camelCase alias generator anywhere in its Pydantic
 * schemas (checked directly) -- every real JSON payload is snake_case
 * (organization_id, created_at, issuer_url, ...), while every frontend type
 * in src/types/* and every mock in src/mocks/* is camelCase, the normal JS
 * convention. apiRequest() converts at the boundary so the rest of the app
 * never has to care which side of that boundary a value came from.
 */

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) && !(value instanceof Date);
}

function snakeToCamelKey(key: string): string {
  return key.replace(/_([a-z0-9])/g, (_, char) => char.toUpperCase());
}

function camelToSnakeKey(key: string): string {
  return key.replace(/[A-Z]/g, (char) => `_${char.toLowerCase()}`);
}

function deepTransformKeys(value: unknown, transformKey: (key: string) => string): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => deepTransformKeys(item, transformKey));
  }
  if (isPlainObject(value)) {
    const result: Record<string, unknown> = {};
    for (const [key, val] of Object.entries(value)) {
      result[transformKey(key)] = deepTransformKeys(val, transformKey);
    }
    return result;
  }
  return value;
}

export function keysToCamelCase<T>(value: unknown): T {
  return deepTransformKeys(value, snakeToCamelKey) as T;
}

export function keysToSnakeCase(value: unknown): unknown {
  return deepTransformKeys(value, camelToSnakeKey);
}
