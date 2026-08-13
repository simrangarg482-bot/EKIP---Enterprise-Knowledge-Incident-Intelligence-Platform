export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

/**
 * Central switch between the mock data layer (src/mocks) and the real
 * FastAPI backend. Flip VITE_USE_MOCK_DATA=false once the endpoints
 * referenced in src/api/* are reachable — no call-site changes required.
 */
export const USE_MOCK_DATA: boolean = import.meta.env.VITE_USE_MOCK_DATA !== "false";
