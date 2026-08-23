import { createApiClient } from "../src/index";
import type { components } from "../src/schema";

const client = createApiClient("http://localhost:8000");
const request = client.GET("/api/v1/health/live");
const health: components["schemas"]["HealthResponse"] = { status: "ok" };

void request;
void health;
