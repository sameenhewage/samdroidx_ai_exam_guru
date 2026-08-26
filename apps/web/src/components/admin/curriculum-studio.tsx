"use client";

import { createApiClient, type components } from "@exam-guru/api-client";
import {
  Children,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { Button, Form, Input, Label, TextField } from "react-aria-components";

type Exam = components["schemas"]["ExamConfigurationResponse"];
type Medium = components["schemas"]["MediumResponse"];
type Subject = components["schemas"]["SubjectResponse"];
type Curriculum = components["schemas"]["CurriculumVersionResponse"];
type TaxonomyNode = components["schemas"]["TaxonomyNodeResponse"];
type AuditEvent = components["schemas"]["AdminAuditEventResponse"];
type Role = "admin" | "reviewer";

const fieldClass = "grid gap-1 text-sm font-medium text-slate-700";
const inputClass =
  "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-slate-950 outline-none focus:border-amber-500 focus:ring-2 focus:ring-amber-200";
const primaryButton =
  "rounded-md bg-slate-950 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButton =
  "rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-semibold text-slate-800 disabled:opacity-50";

function errorCode(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail?: unknown }).detail;
    if (detail && typeof detail === "object" && "code" in detail) {
      return String((detail as { code: unknown }).code);
    }
  }
  return "request_failed";
}

export function CurriculumStudio({ role }: { role: Role }) {
  const api = useMemo(
    () => createApiClient(globalThis.location?.origin ?? "http://localhost"),
    [],
  );
  const [exams, setExams] = useState<Exam[]>([]);
  const [media, setMedia] = useState<Medium[]>([]);
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [curricula, setCurricula] = useState<Curriculum[]>([]);
  const [nodes, setNodes] = useState<TaxonomyNode[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [selectedCurriculumId, setSelectedCurriculumId] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [operationError, setOperationError] = useState("");
  const error = operationError || loadError;
  const canWrite = role === "admin";

  const loadConfiguration = useCallback(async () => {
    setLoading(true);
    const [examResult, mediumResult, subjectResult, curriculumResult, auditResult] =
      await Promise.all([
        api.GET("/api/v1/admin/exam-configurations"),
        api.GET("/api/v1/admin/media"),
        api.GET("/api/v1/admin/subjects"),
        api.GET("/api/v1/admin/curriculum-versions"),
        api.GET("/api/v1/admin/audit-events", { params: { query: { limit: 30 } } }),
      ]);
    const firstError =
      examResult.error ??
      mediumResult.error ??
      subjectResult.error ??
      curriculumResult.error ??
      auditResult.error;
    if (firstError) {
      setLoadError(errorCode(firstError));
    } else {
      setExams(examResult.data ?? []);
      setMedia(mediumResult.data ?? []);
      setSubjects(subjectResult.data ?? []);
      const nextCurricula = curriculumResult.data ?? [];
      setCurricula(nextCurricula);
      setAuditEvents(auditResult.data ?? []);
      setSelectedCurriculumId((current) => current || nextCurricula.find((item) => item.active)?.id || "");
      setLoadError("");
    }
    setLoading(false);
  }, [api]);

  const loadNodes = useCallback(
    async (curriculumVersionId: string) => {
      if (!curriculumVersionId) {
        setNodes([]);
        return;
      }
      const result = await api.GET(
        "/api/v1/admin/curricula/{curriculum_version_id}/taxonomy/nodes",
        { params: { path: { curriculum_version_id: curriculumVersionId } } },
      );
      if (result.error) {
        setLoadError(errorCode(result.error));
      } else {
        setNodes(result.data ?? []);
        setLoadError("");
      }
    },
    [api],
  );

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadConfiguration(), 0);
    return () => window.clearTimeout(timeout);
  }, [loadConfiguration]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadNodes(selectedCurriculumId), 0);
    return () => window.clearTimeout(timeout);
  }, [loadNodes, selectedCurriculumId]);

  async function refresh() {
    await loadConfiguration();
    if (selectedCurriculumId) {
      await loadNodes(selectedCurriculumId);
    }
  }

  async function createExam(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setOperationError("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const result = await api.POST("/api/v1/admin/exam-configurations", {
      body: {
        code: String(form.get("code")),
        grade: Number(form.get("grade")),
        name: String(form.get("name")),
      },
    });
    if (result.error) setOperationError(errorCode(result.error));
    else {
      formElement.reset();
      await refresh();
    }
  }

  async function createMedium(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setOperationError("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const result = await api.POST("/api/v1/admin/media", {
      body: { code: String(form.get("code")), name: String(form.get("name")) },
    });
    if (result.error) setOperationError(errorCode(result.error));
    else {
      formElement.reset();
      await refresh();
    }
  }

  async function createSubject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setOperationError("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const result = await api.POST("/api/v1/admin/subjects", {
      body: { code: String(form.get("code")), name: String(form.get("name")) },
    });
    if (result.error) setOperationError(errorCode(result.error));
    else {
      formElement.reset();
      await refresh();
    }
  }

  async function createCurriculum(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setOperationError("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const result = await api.POST("/api/v1/admin/curriculum-versions", {
      body: {
        code: String(form.get("code")),
        exam_configuration_id: String(form.get("exam_configuration_id")),
        medium_id: String(form.get("medium_id")),
        subject_id: String(form.get("subject_id")),
        title: String(form.get("title")),
      },
    });
    if (result.error) setOperationError(errorCode(result.error));
    else {
      formElement.reset();
      await refresh();
    }
  }

  async function createNode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setOperationError("");
    if (!selectedCurriculumId) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const parentId = String(form.get("parent_id") ?? "");
    const result = await api.POST(
      "/api/v1/admin/curricula/{curriculum_version_id}/taxonomy/nodes",
      {
        body: {
          active: true,
          code: String(form.get("code")),
          level: form.get("level") as components["schemas"]["TaxonomyLevel"],
          parent_id: parentId || null,
          title: String(form.get("title")),
        },
        params: { path: { curriculum_version_id: selectedCurriculumId } },
      },
    );
    if (result.error) setOperationError(errorCode(result.error));
    else {
      formElement.reset();
      await refresh();
    }
  }

  async function updateConfiguration(
    resource: "exam" | "medium" | "subject" | "curriculum",
    id: string,
    value: string,
  ) {
    setOperationError("");
    const result =
      resource === "exam"
        ? await api.PATCH("/api/v1/admin/exam-configurations/{resource_id}", {
            body: { name: value },
            params: { path: { resource_id: id } },
          })
        : resource === "medium"
          ? await api.PATCH("/api/v1/admin/media/{resource_id}", {
              body: { name: value },
              params: { path: { resource_id: id } },
            })
          : resource === "subject"
            ? await api.PATCH("/api/v1/admin/subjects/{resource_id}", {
                body: { name: value },
                params: { path: { resource_id: id } },
              })
            : await api.PATCH("/api/v1/admin/curriculum-versions/{resource_id}", {
                body: { title: value },
                params: { path: { resource_id: id } },
              });
    if (result.error) setOperationError(errorCode(result.error));
    else await refresh();
  }

  async function deactivateConfiguration(
    resource: "exam" | "medium" | "subject" | "curriculum",
    id: string,
  ) {
    setOperationError("");
    const result =
      resource === "exam"
        ? await api.POST("/api/v1/admin/exam-configurations/{resource_id}/deactivate", {
            params: { path: { resource_id: id } },
          })
        : resource === "medium"
          ? await api.POST("/api/v1/admin/media/{resource_id}/deactivate", {
              params: { path: { resource_id: id } },
            })
          : resource === "subject"
            ? await api.POST("/api/v1/admin/subjects/{resource_id}/deactivate", {
                params: { path: { resource_id: id } },
              })
            : await api.POST("/api/v1/admin/curriculum-versions/{resource_id}/deactivate", {
                params: { path: { resource_id: id } },
              });
    if (result.error) setOperationError(errorCode(result.error));
    else await refresh();
  }

  async function updateNode(event: FormEvent<HTMLFormElement>, nodeId: string) {
    event.preventDefault();
    setOperationError("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const result = await api.PATCH(
      "/api/v1/admin/curricula/{curriculum_version_id}/taxonomy/nodes/{node_id}",
      {
        body: { code: String(form.get("code")), title: String(form.get("title")) },
        params: { path: { curriculum_version_id: selectedCurriculumId, node_id: nodeId } },
      },
    );
    if (result.error) setOperationError(errorCode(result.error));
    else await refresh();
  }

  async function transitionNode(nodeId: string, action: "review" | "deactivate") {
    setOperationError("");
    const result =
      action === "review"
        ? await api.POST(
            "/api/v1/admin/curricula/{curriculum_version_id}/taxonomy/nodes/{node_id}/review",
            { params: { path: { curriculum_version_id: selectedCurriculumId, node_id: nodeId } } },
          )
        : await api.POST(
            "/api/v1/admin/curricula/{curriculum_version_id}/taxonomy/nodes/{node_id}/deactivate",
            { params: { path: { curriculum_version_id: selectedCurriculumId, node_id: nodeId } } },
          );
    if (result.error) setOperationError(errorCode(result.error));
    else await refresh();
  }

  return (
    <div className="mx-auto max-w-7xl px-5 py-8 sm:px-8">
      <header className="border-b border-slate-300 pb-6">
        <p className="font-mono text-xs tracking-[0.18em] text-slate-500 uppercase">P1 admin workflow</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight">Configuration &amp; taxonomy</h1>
        <p className="mt-2 max-w-3xl text-slate-600">
          Manage grades, media, subjects, curriculum versions and reviewed taxonomy with
          transactional audit evidence.
        </p>
        {role === "reviewer" && <p className="mt-3 text-sm font-semibold text-amber-800">Reviewer access is read-only.</p>}
        {error && <p className="mt-4 border border-red-300 bg-red-50 p-3 text-sm text-red-800" role="alert">{error}</p>}
      </header>

      {loading ? <p className="py-8">Loading configuration…</p> : (
        <div className="mt-8 grid gap-8 md:grid-cols-2 xl:grid-cols-4">
          <ConfigSection title="Exam configurations">
            {canWrite && <Form className="grid gap-3" onSubmit={createExam}>
              <TextField className={fieldClass} name="code" isRequired><Label>Exam code</Label><Input className={inputClass} /></TextField>
              <TextField className={fieldClass} name="name" isRequired><Label>Exam name</Label><Input className={inputClass} /></TextField>
              <label className={fieldClass}>Grade<input className={inputClass} defaultValue="5" max={13} min={1} name="grade" required type="number" /></label>
              <Button className={primaryButton} type="submit">Create exam</Button>
            </Form>}
            <ConfigList empty="No exam configurations yet.">
              {exams.map((exam) => <ConfigCard active={exam.active} id={exam.id} key={exam.id} label={exam.name} subtitle={`Grade ${exam.grade}`} value={exam.name} canWrite={canWrite} onSave={(value) => updateConfiguration("exam", exam.id, value)} onDeactivate={() => deactivateConfiguration("exam", exam.id)} />)}
            </ConfigList>
          </ConfigSection>

          <ConfigSection title="Media">
            {canWrite && <Form className="grid gap-3" onSubmit={createMedium}>
              <TextField className={fieldClass} name="code" isRequired><Label>Medium code</Label><Input className={inputClass} /></TextField>
              <TextField className={fieldClass} name="name" isRequired><Label>Medium name</Label><Input className={inputClass} /></TextField>
              <Button className={primaryButton} type="submit">Create medium</Button>
            </Form>}
            <ConfigList empty="No media yet.">
              {media.map((item) => <ConfigCard active={item.active} id={item.id} key={item.id} label={item.name} value={item.name} canWrite={canWrite} onSave={(value) => updateConfiguration("medium", item.id, value)} onDeactivate={() => deactivateConfiguration("medium", item.id)} />)}
            </ConfigList>
          </ConfigSection>

          <ConfigSection title="Subjects">
            {canWrite && <Form className="grid gap-3" onSubmit={createSubject}>
              <TextField className={fieldClass} name="code" isRequired><Label>Subject code</Label><Input className={inputClass} /></TextField>
              <TextField className={fieldClass} name="name" isRequired><Label>Subject name</Label><Input className={inputClass} /></TextField>
              <Button className={primaryButton} type="submit">Create subject</Button>
            </Form>}
            <ConfigList empty="No subjects yet.">
              {subjects.map((item) => <ConfigCard active={item.active} id={item.id} key={item.id} label={item.name} subtitle={item.code} value={item.name} canWrite={canWrite} onSave={(value) => updateConfiguration("subject", item.id, value)} onDeactivate={() => deactivateConfiguration("subject", item.id)} />)}
            </ConfigList>
          </ConfigSection>

          <ConfigSection title="Curriculum versions">
            {canWrite && <Form className="grid gap-3" onSubmit={createCurriculum}>
              <label className={fieldClass}>Exam<select className={inputClass} name="exam_configuration_id" required>{exams.filter((item) => item.active).map((item) => <option key={item.id} value={item.id}>{item.name} · Grade {item.grade}</option>)}</select></label>
              <label className={fieldClass}>Medium<select className={inputClass} name="medium_id" required>{media.filter((item) => item.active).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
              <label className={fieldClass}>Curriculum subject<select className={inputClass} name="subject_id" required>{subjects.filter((item) => item.active).map((item) => <option key={item.id} value={item.id}>{item.name} ({item.code})</option>)}</select></label>
              <TextField className={fieldClass} name="code" isRequired><Label>Curriculum code</Label><Input className={inputClass} /></TextField>
              <TextField className={fieldClass} name="title" isRequired><Label>Curriculum title</Label><Input className={inputClass} /></TextField>
              <Button className={primaryButton} isDisabled={!exams.some((item) => item.active) || !media.some((item) => item.active) || !subjects.some((item) => item.active)} type="submit">Create curriculum</Button>
            </Form>}
            <ConfigList empty="No curriculum versions yet.">
              {curricula.map((item) => {
                const exam = exams.find((value) => value.id === item.exam_configuration_id);
                const medium = media.find((value) => value.id === item.medium_id);
                const subject = subjects.find((value) => value.id === item.subject_id);
                return <ConfigCard active={item.active} id={item.id} key={item.id} label={item.title} subtitle={`${subject?.name ?? "Unknown subject"} · Grade ${exam?.grade ?? "?"} · ${medium?.name ?? "Unknown medium"}`} value={item.title} canWrite={canWrite} onSelect={() => setSelectedCurriculumId(item.id)} onSave={(value) => updateConfiguration("curriculum", item.id, value)} onDeactivate={() => deactivateConfiguration("curriculum", item.id)} />;
              })}
            </ConfigList>
          </ConfigSection>
        </div>
      )}

      <section className="mt-10 border-t border-slate-300 pt-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div><p className="font-mono text-xs text-slate-500 uppercase">Selected curriculum</p><h2 className="mt-1 text-2xl font-semibold">Taxonomy lifecycle</h2></div>
          <select aria-label="Selected curriculum" className={inputClass} onChange={(event) => setSelectedCurriculumId(event.target.value)} value={selectedCurriculumId}><option value="">Choose curriculum</option>{curricula.map((item) => <option key={item.id} value={item.id}>{item.title} — {subjects.find((subject) => subject.id === item.subject_id)?.name ?? "Unknown subject"}</option>)}</select>
        </div>
        {canWrite && selectedCurriculumId && <Form className="mt-6 grid gap-3 rounded-lg border border-slate-300 bg-white p-4 md:grid-cols-5" onSubmit={createNode}>
          <label className={fieldClass}>Level<select className={inputClass} name="level"><option value="competency">Competency</option><option value="skill">Skill</option><option value="sub_skill">Sub-skill</option><option value="learning_concept">Learning concept</option></select></label>
          <label className={fieldClass}>Parent<select className={inputClass} name="parent_id"><option value="">None</option>{nodes.filter((node) => node.active).map((node) => <option key={node.id} value={node.id}>{node.code} — {node.title}</option>)}</select></label>
          <TextField className={fieldClass} name="code" isRequired><Label>Taxonomy code</Label><Input className={inputClass} /></TextField>
          <TextField className={fieldClass} name="title" isRequired><Label>Taxonomy title</Label><Input className={inputClass} /></TextField>
          <Button className={`${primaryButton} self-end`} type="submit">Create taxonomy node</Button>
        </Form>}
        <div className="mt-5 grid gap-3 md:grid-cols-2">
          {nodes.map((node) => <article className="rounded-lg border border-slate-300 bg-white p-4" key={node.id}>
            <div className="flex items-center justify-between gap-3"><div><p className="font-mono text-xs uppercase text-slate-500">{node.level}</p><h3 className="font-semibold">{node.code} — {node.title}</h3></div><span className="rounded-full bg-slate-100 px-2 py-1 text-xs">{node.review_state}</span></div>
            {canWrite && node.review_state === "draft" && <form className="mt-4 grid gap-2 sm:grid-cols-[1fr_2fr_auto]" onSubmit={(event) => updateNode(event, node.id)}><label className={fieldClass}>Code<input className={inputClass} defaultValue={node.code} name="code" /></label><label className={fieldClass}>Title<input className={inputClass} defaultValue={node.title} name="title" /></label><button className={`${secondaryButton} self-end`} type="submit">Save node</button></form>}
            <div className="mt-4 flex gap-2">{node.review_state === "draft" && <button className={secondaryButton} onClick={() => transitionNode(node.id, "review")} type="button">Review node</button>}{canWrite && node.review_state !== "deprecated" && <button className={secondaryButton} onClick={() => transitionNode(node.id, "deactivate")} type="button">Deactivate node</button>}</div>
          </article>)}
          {selectedCurriculumId && !nodes.length && <p className="text-slate-500">No taxonomy nodes yet.</p>}
        </div>
      </section>

      <section className="mt-10 border-t border-slate-300 pt-8" aria-labelledby="audit-heading">
        <h2 className="text-2xl font-semibold" id="audit-heading">Recent audit evidence</h2>
        <div className="mt-4 overflow-x-auto rounded-lg border border-slate-300 bg-white">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-100 text-xs uppercase text-slate-500"><tr><th className="px-4 py-3">Action</th><th className="px-4 py-3">Resource</th><th className="px-4 py-3">Actor</th><th className="px-4 py-3">Time</th></tr></thead>
            <tbody>{auditEvents.map((event) => <tr className="border-t border-slate-200" key={event.id}><td className="px-4 py-3 font-mono">{event.action}</td><td className="px-4 py-3">{event.resource_type} · {event.resource_id.slice(0, 8)}</td><td className="px-4 py-3">{event.actor_id.slice(0, 8)}</td><td className="px-4 py-3">{new Date(event.created_at).toLocaleString()}</td></tr>)}</tbody>
          </table>
          {!auditEvents.length && <p className="p-4 text-slate-500">No audit events yet.</p>}
        </div>
      </section>
    </div>
  );
}

function ConfigSection({ children, title }: { children: ReactNode; title: string }) {
  return <section className="rounded-lg border border-slate-300 bg-white p-5"><h2 className="text-xl font-semibold">{title}</h2><div className="mt-5 grid gap-5">{children}</div></section>;
}

function ConfigList({ children, empty }: { children: ReactNode; empty: string }) {
  return (
    <div className="grid gap-3">
      {Children.count(children) ? children : <p className="text-sm text-slate-500">{empty}</p>}
    </div>
  );
}

function ConfigCard({ active, canWrite, id, label, onDeactivate, onSave, onSelect, subtitle, value }: { active: boolean; canWrite: boolean; id: string; label: string; onDeactivate: () => void; onSave: (value: string) => void; onSelect?: () => void; subtitle?: string; value: string }) {
  return <article className="rounded-md border border-slate-200 bg-slate-50 p-3"><button className="text-left font-semibold" onClick={onSelect} type="button">{label}</button>{subtitle ? <p className="mt-1 text-xs font-medium text-slate-600">{subtitle}</p> : null}<p className="text-xs text-slate-500">{active ? "active" : "deprecated"} · {id.slice(0, 8)}</p>{canWrite && active && <form className="mt-3 flex gap-2" onSubmit={(event) => { event.preventDefault(); onSave(String(new FormData(event.currentTarget).get("value"))); }}><label className="sr-only" htmlFor={`value-${id}`}>Update {label}</label><input className={inputClass} defaultValue={value} id={`value-${id}`} name="value" /><button className={secondaryButton} type="submit">Save</button><button className={secondaryButton} onClick={onDeactivate} type="button">Deactivate</button></form>}</article>;
}
