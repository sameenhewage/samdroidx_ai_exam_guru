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
const paperJob = client.POST("/api/v1/admin/paper-generation/jobs", {
  body: {
    target: {
      grade: 5,
      medium: "si",
      paper_type: "subject_practice",
      subject: "MATHEMATICS",
    },
    scope: { end_lesson: 3, kind: "lesson_range", start_lesson: 1 },
    settings: {
      difficulty: "balanced",
      duration_minutes: 50,
      mcq_count: 5,
      paper_name: "Grade 5 Mathematics practice",
      structured_count: 0,
      written_count: 7,
    },
  },
  params: { header: { "Idempotency-Key": "teacher-paper-request-1" } },
});
const paperProgress = client.GET("/api/v1/admin/paper-generation/jobs/{paper_job_id}", {
  params: { path: { paper_job_id: "00000000-0000-0000-0000-000000000801" } },
});
const reviewPaper = client.GET("/api/v1/admin/review-papers/{paper_job_id}", {
  params: { path: { paper_job_id: "00000000-0000-0000-0000-000000000801" } },
});
const regenerateQuestion = client.POST(
  "/api/v1/admin/review-papers/{paper_job_id}/questions/{question_id}/regenerate",
  {
    body: {
      expected_version: 4,
      note: "Prepare and validate a replacement.",
      reason_code: "answer_incorrect",
    },
    params: {
      header: { "Idempotency-Key": "replacement-1" },
      path: {
        paper_job_id: "00000000-0000-0000-0000-000000000801",
        question_id: "00000000-0000-0000-0000-000000000811",
      },
    },
  },
);
const promoteFeedback = client.POST(
  "/api/v1/admin/subject-quality/feedback/{feedback_id}/promote",
  {
    body: {
      defect_category: "answer_correctness",
      expected_finding_codes: ["subject.math.answer_mismatch"],
      expected_status: "fail",
    },
    params: {
      header: { "Idempotency-Key": "promote-feedback-1" },
      path: { feedback_id: "00000000-0000-0000-0000-000000000812" },
    },
  },
);
const intent: components["schemas"]["TeacherPaperJobCreateRequest"] = {
  target: {
    grade: 5,
    medium: "si",
    paper_type: "subject_practice",
    subject: "MATHEMATICS",
  },
  scope: { kind: "full_subject" },
  settings: {
    difficulty: "challenging",
    duration_minutes: 60,
    mcq_count: 10,
    paper_name: "Grade 5 Mathematics challenge",
    structured_count: 2,
    written_count: 8,
  },
};
const health: components["schemas"]["HealthResponse"] = { status: "ok" };
const session: components["schemas"]["AuthSessionResponse"] = {
  roles: ["admin", "reviewer"],
  subject_id: "b53b84f8-a97b-5d84-b028-76f6f60539a5",
};

void request;
void sessionRequest;
void taxonomyRequest;
void paperJob;
void paperProgress;
void reviewPaper;
void regenerateQuestion;
void promoteFeedback;
void intent;
void health;
void session;
