import { createApiClient } from "../src/index";
import type { components } from "../src/schema";

const client = createApiClient("http://localhost:8000");
const request = client.GET("/api/v1/health/live");
const sessionRequest = client.GET("/api/v1/auth/session");
const taxonomyRequest = client.POST(
  "/api/v1/admin/curricula/{curriculum_version_id}/taxonomy/nodes",
  {
    body: {
      active: true,
      code: "C1",
      level: "competency",
      title: "Competency 1",
    },
    params: { path: { curriculum_version_id: "00000000-0000-0000-0000-000000000001" } },
  },
);
const health: components["schemas"]["HealthResponse"] = { status: "ok" };
const session: components["schemas"]["AuthSessionResponse"] = {
  roles: ["admin", "reviewer"],
  subject_id: "b53b84f8-a97b-5d84-b028-76f6f60539a5",
};

void request;
void sessionRequest;
void taxonomyRequest;
void health;
void session;
