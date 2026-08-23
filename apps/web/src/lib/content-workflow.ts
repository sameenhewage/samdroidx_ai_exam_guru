export const contentWorkflow = [
  {
    id: "curriculum",
    label: "Curriculum",
    description: "Grade 5 versions, media, competencies, skills, and learning concepts.",
  },
  {
    id: "documents",
    label: "Documents",
    description: "Trusted source uploads, extraction state, and immutable provenance.",
  },
  {
    id: "extraction-review",
    label: "Extraction review",
    description: "Page-level comparison, correction, and promotion to trusted content.",
  },
  {
    id: "historical-questions",
    label: "Historical questions",
    description: "Normalized past questions, classifications, answers, and source links.",
  },
  {
    id: "rag-explorer",
    label: "RAG explorer",
    description: "Scoped lexical and semantic retrieval with visible grounding evidence.",
  },
  {
    id: "exam-intelligence",
    label: "Exam intelligence",
    description: "Historical coverage, practice priorities, backtests, and baseline comparison.",
  },
  {
    id: "blueprints",
    label: "Blueprints",
    description: "Deterministic paper structure, constraints, coverage, and rationale.",
  },
  {
    id: "generation",
    label: "Generation",
    description: "Grounded candidate runs, provider metadata, cost, latency, and failures.",
  },
  {
    id: "review-queue",
    label: "Review queue",
    description: "Validation evidence, source context, edits, approvals, and rejections.",
  },
  {
    id: "papers",
    label: "Papers",
    description: "Approved question assembly and immutable publication versions.",
  },
] as const;
