import { createApiClient, type components } from "@exam-guru/api-client";
import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ResumableSourceUpload,
  UPLOAD_CHUNK_BYTES,
  UploadFailure,
  uploadFailureMessage,
  recoverUploadCheckpoints,
} from "./source-upload";
import {
  readUploadCheckpoints,
  recordUploadRequest,
  UPLOAD_CHECKPOINT_KEY,
} from "./source-upload-checkpoint";

type Session = components["schemas"]["SourceUploadResponse"];
type Receipt = components["schemas"]["SourceUploadChunkReceipt"];
type ReceiptPage = components["schemas"]["SourceUploadChunkPageResponse"];
type RequestBody = components["schemas"]["SourceUploadCreateRequest"];
const id = "00000000-0000-0000-0000-000000000961";
const documentId = "00000000-0000-0000-0000-000000000962";
const readJobId = "00000000-0000-0000-0000-000000000963";
const chunkBytes = 4_194_304;
const metadata = {
  document_type: "past_paper",
  curriculum_version_id: "00000000-0000-0000-0000-000000000964",
  unit_id: null,
  lesson_id: null,
  year: 2025,
  paper_code: null,
} satisfies Omit<
  components["schemas"]["SourceUploadCreateRequest"],
  "filename" | "size_bytes"
>;

function fakePdf(size = chunkBytes * 2 + 13, changedChunk = -1): File {
  return {
    name: "same-name.pdf",
    size,
    type: "application/pdf",
    lastModified: 12345,
    arrayBuffer: vi.fn(() => {
      throw new Error("Never read the complete File");
    }),
    slice: vi.fn((start = 0, end = size) => {
      if (end - start > chunkBytes) throw new Error("Unbounded File.slice");
      const data = new Uint8Array(end - start).fill(
        Math.floor(start / chunkBytes) === changedChunk ? 99 : 32,
      );
      if (start === 0) data.set(new TextEncoder().encode("%PDF-"));
      return {
        size: data.length,
        arrayBuffer: async () => data.buffer,
      } as Blob;
    }),
  } as unknown as File;
}

async function digest(bytes: ArrayBuffer) {
  return [...new Uint8Array(await webcrypto.subtle.digest("SHA-256", bytes))]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

type Fault =
  | "lost-create"
  | "unaccepted-create"
  | "lost-chunk"
  | "chunk-down"
  | "lost-complete";
async function fixture(
  options: {
    file?: File;
    prefix?: number;
    requestId?: string;
    status?: Session["status"];
    fault?: Fault;
    error?: {
      status: number;
      code: string;
      retryAfter?: string;
      method?: string;
    };
    neverComplete?: boolean;
    badReceipts?: boolean;
    deduplicated?: boolean;
  } = {},
) {
  const file = options.file ?? fakePdf();
  let session: Session = {
    id,
    request_id: options.requestId,
    filename: file.name,
    size_bytes: file.size,
    document_type: "past_paper",
    intake_metadata: {},
    status: options.status ?? "uploading",
    next_offset: options.prefix ?? 0,
    verified_bytes: 0,
    version: 7,
    chunk_size_bytes: chunkBytes,
    deduplicated: false,
    created_at: "2026-09-06T00:00:00Z",
    updated_at: "2026-09-06T00:00:00Z",
  };
  const receipts: Receipt[] = [];
  for (let offset = 0; offset < session.next_offset; offset += chunkBytes) {
    const bytes = await file
      .slice(offset, Math.min(offset + chunkBytes, file.size))
      .arrayBuffer();
    receipts.push({
      offset,
      size_bytes: bytes.byteLength,
      checksum_sha256: await digest(bytes),
    });
  }
  const requests: Array<{
    method: string;
    path: string;
    offset: string | null;
    checksum: string | null;
    bytes?: number;
    json?: unknown;
  }> = [];
  let faultUsed = false;
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const request =
        input instanceof Request ? input : new Request(input, init);
      const url = new URL(request.url);
      const record = {
        method: request.method,
        path: url.pathname,
        offset: url.searchParams.get("offset"),
        checksum: request.headers.get("X-Chunk-SHA256"),
      } as (typeof requests)[number];
      requests.push(record);
      if (
        options.error &&
        (!options.error.method || request.method === options.error.method)
      ) {
        return Response.json(
          { detail: { code: options.error.code } },
          {
            status: options.error.status,
            headers: options.error.retryAfter
              ? { "Retry-After": options.error.retryAfter }
              : {},
          },
        );
      }
      if (
        request.method === "POST" &&
        url.pathname.endsWith("/source-uploads")
      ) {
        const body = (await request.json()) as { request_id?: string };
        record.json = body;
        if (options.fault === "unaccepted-create" && !faultUsed) {
          faultUsed = true;
          throw new TypeError("connection lost before commit");
        }
        session = { ...session, request_id: body.request_id };
        if (options.fault === "lost-create" && !faultUsed) {
          faultUsed = true;
          throw new TypeError("connection lost after commit");
        }
        return Response.json(session, { status: 201 });
      }
      if (request.method === "GET" && url.pathname.includes("/by-request/")) {
        if (session.request_id !== url.pathname.split("/").at(-1))
          return Response.json(
            { detail: { code: "source_upload_not_found" } },
            { status: 404 },
          );
        return Response.json(session);
      }
      if (
        request.method === "GET" &&
        url.pathname.endsWith(`/source-uploads/${id}`)
      ) {
        if (session.status === "pending" && !options.neverComplete) {
          session = {
            ...session,
            status: "completed",
            document_id: documentId,
            source_read_job_id: readJobId,
            verified_bytes: file.size,
            checksum_sha256: "a".repeat(64),
            deduplicated: options.deduplicated ?? false,
          };
        }
        return Response.json(session);
      }
      if (request.method === "GET" && url.pathname.endsWith("/chunks")) {
        expect(url.searchParams.get("limit")).toBe("64");
        const matches = receipts.filter(
          (receipt) => receipt.offset >= Number(record.offset),
        );
        const page = matches.slice(0, 64);
        return Response.json({
          upload_id: id,
          next_offset: session.next_offset,
          receipts: options.badReceipts ? [] : page,
          next_receipt_offset:
            matches.length > 64
              ? page.at(-1)!.offset + page.at(-1)!.size_bytes
              : null,
        });
      }
      if (request.method === "PUT" && url.pathname.endsWith("/chunks")) {
        expect(request.headers.get("Content-Type")).toBe(
          "application/octet-stream",
        );
        const bytes = await request.arrayBuffer();
        record.bytes = bytes.byteLength;
        expect(bytes.byteLength).toBeLessThanOrEqual(chunkBytes);
        expect(record.checksum).toBe(await digest(bytes));
        if (options.fault === "chunk-down" && !faultUsed) {
          faultUsed = true;
          throw new TypeError("connection lost before commit");
        }
        expect(Number(record.offset)).toBe(session.next_offset);
        receipts.push({
          offset: session.next_offset,
          size_bytes: bytes.byteLength,
          checksum_sha256: record.checksum!,
        });
        session = {
          ...session,
          next_offset: session.next_offset + bytes.byteLength,
          version: session.version + 1,
        };
        if (options.fault === "lost-chunk" && !faultUsed) {
          faultUsed = true;
          throw new TypeError("connection lost after commit");
        }
        return Response.json(session);
      }
      if (request.method === "POST" && url.pathname.endsWith("/complete")) {
        record.json = await request.json();
        expect(record.json).toEqual({ expected_version: session.version });
        session = {
          ...session,
          status: "pending",
          version: session.version + 1,
        };
        if (options.fault === "lost-complete" && !faultUsed) {
          faultUsed = true;
          throw new TypeError("completion response lost");
        }
        return Response.json(session, { status: 202 });
      }
      return Response.json(
        { detail: { code: "unexpected_request" } },
        { status: 500 },
      );
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return {
    file,
    requests,
    getSession: () => session,
    api: createApiClient("http://localhost"),
    fetchMock,
  };
}
function interceptResponses(
  f: Awaited<ReturnType<typeof fixture>>,
  transform: (
    request: Request,
    response: Response,
  ) => Response | Promise<Response>,
) {
  const serve = f.fetchMock.getMockImplementation()!;
  f.fetchMock.mockImplementation(async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    return transform(request, await serve(request));
  });
}

function httpFailure(status: number, code: string, headers?: HeadersInit) {
  return Response.json({ detail: { code } }, { status, headers });
}

const runOptions = { pollIntervalMs: 0, maxPolls: 3 };
const requestId = "00000000-0000-0000-0000-000000000965";
const root = "/api/v1/admin/source-uploads";

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("crypto", webcrypto);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("upload failure guidance", () => {
  it.each([
    [new Error("private-file.pdf"), "Your saved progress has been kept"],
    [
      new UploadFailure("source_upload_request_conflict", 409),
      "details differ from the saved upload request",
    ],
    [new UploadFailure("upload_file_required"), "Choose the original PDF"],
    [new UploadFailure("upload_receipts_invalid"), "Nothing more was uploaded"],
    [new UploadFailure("upload_changed"), "Refresh its progress"],
    [
      new UploadFailure("upload_still_finishing"),
      "still finishing this upload",
    ],
    [
      new UploadFailure("upload_crypto_unavailable"),
      "localhost or a secure connection",
    ],
    [new UploadFailure("source_upload_not_found", 404), "original account"],
    [new UploadFailure("rate_limit_exceeded", 429), "wait before continuing"],
    [new UploadFailure("rate_limit_exceeded", 429, 0), "wait 0 seconds"],
    [new UploadFailure("upload_failed"), "no text has been trusted"],
    [new UploadFailure("invalid_metadata", 422), "readable PDF"],
    [new UploadFailure("upload_invalid_file"), "details are correct"],
    [new UploadFailure("upload_response_invalid"), "unexpected upload status"],
    [
      new UploadFailure("private-file.pdf", 502),
      "connection or upload service was interrupted",
    ],
  ])("provides safe actionable guidance for %s", (error, text) => {
    const message = uploadFailureMessage(error);
    expect(message).toContain(text);
    expect(message).not.toContain("private-file.pdf");
  });
});

describe("upload protocol boundaries", () => {
  it.each([
    ["malformed session ID", { id: "../upload" }],
    ["another session ID", { id: documentId }],
    ["fractional size", { size_bytes: 20.5 }],
    ["unsafe size", { size_bytes: Number.MAX_SAFE_INTEGER + 1 }],
    ["too-small size", { size_bytes: 4 }],
    ["negative offset", { next_offset: -1 }],
    ["offset beyond size", { next_offset: 40 }],
    ["unaligned offset", { next_offset: 1 }],
    ["invalid version", { version: -1 }],
    ["invalid verified bytes", { verified_bytes: 0.5 }],
    ["verified bytes beyond size", { verified_bytes: 21 }],
    ["unsupported chunk size", { chunk_size_bytes: chunkBytes / 2 }],
    ["unknown state", { status: "ready" }],
  ])("rejects %s without any upload mutation", async (_name, invalid) => {
    const f = await fixture({ file: fakePdf(20) });
    interceptResponses(f, async (_request, response) =>
      Response.json({ ...(await response.json()), ...invalid }),
    );
    await expect(
      new ResumableSourceUpload({ api: f.api, uploadId: id }).run(runOptions),
    ).rejects.toMatchObject({ code: "upload_response_invalid" });
    expect(f.requests.map((request) => request.method)).toEqual(["GET"]);
  });

  it.each([
    ["missing document", { document_id: null }],
    ["invalid document", { document_id: "../document" }],
    ["missing accepted bytes", { next_offset: 0 }],
    ["unverified bytes", { verified_bytes: 19 }],
    ["missing checksum", { checksum_sha256: null }],
    ["invalid checksum", { checksum_sha256: "A".repeat(64) }],
    ["invalid read job", { source_read_job_id: "not-a-job" }],
  ])("does not report completion with %s", async (_name, invalid) => {
    const f = await fixture({
      file: fakePdf(20),
      prefix: 20,
      status: "pending",
    });
    interceptResponses(f, async (_request, response) =>
      Response.json({ ...(await response.json()), ...invalid }),
    );
    const task = new ResumableSourceUpload({ api: f.api, uploadId: id });
    await expect(task.run(runOptions)).rejects.toMatchObject({
      code: "upload_response_invalid",
    });
    expect(task.uploadId).toBe(id);
    expect(f.requests.map((request) => request.method)).toEqual(["GET"]);
  });

  it.each([null, undefined])(
    "accepts a verified deduplicated completion without a read job (%s)",
    async (source_read_job_id) => {
      const f = await fixture({
        file: fakePdf(20),
        prefix: 20,
        status: "pending",
        deduplicated: true,
      });
      interceptResponses(f, async (_request, response) =>
        Response.json({ ...(await response.json()), source_read_job_id }),
      );
      await expect(
        new ResumableSourceUpload({ api: f.api, uploadId: id }).run(runOptions),
      ).resolves.toMatchObject({
        document_id: documentId,
        deduplicated: true,
        status: "completed",
      });
      expect(f.requests.map((request) => request.method)).toEqual(["GET"]);
    },
  );

  it.each([
    ["wrong media type", { type: "text/plain" }],
    ["wrong extension", { name: "material.txt" }],
    ["short file", { size: 4 }],
    ["unsafe file size", { size: Number.MAX_SAFE_INTEGER + 1 }],
  ])(
    "rejects a selected PDF with %s before reserving capacity",
    async (_name, fields) => {
      const f = await fixture({ file: fakePdf(20) });
      const file = Object.assign(f.file, fields);
      await expect(
        new ResumableSourceUpload({ api: f.api, file, metadata }).run(
          runOptions,
        ),
      ).rejects.toMatchObject({ code: "upload_invalid_file" });
      expect(f.requests).toEqual([]);
      expect(file.slice).not.toHaveBeenCalled();
      expect(readUploadCheckpoints()).toEqual({
        uploadIds: [],
        creationUncertain: false,
      });
    },
  );

  it.each(["uploadId", "requestId"] as const)(
    "rejects an invalid %s before any network or storage write",
    async (field) => {
      const f = await fixture({ file: fakePdf(20) });
      expect(
        () =>
          new ResumableSourceUpload({ api: f.api, [field]: "../private.pdf" }),
      ).toThrow(
        field === "requestId"
          ? "upload_checkpoint_invalid"
          : "upload_response_invalid",
      );
      expect(f.requests).toEqual([]);
      expect(readUploadCheckpoints()).toEqual({
        uploadIds: [],
        creationUncertain: false,
      });
    },
  );

  it.each([false, true])(
    "requires both a File and reviewed metadata for a new request (has File: %s)",
    async (hasFile) => {
      const f = await fixture({ file: fakePdf(20) });
      await expect(
        new ResumableSourceUpload({
          api: f.api,
          ...(hasFile ? { file: f.file } : { metadata }),
        }).run(runOptions),
      ).rejects.toMatchObject({ code: "upload_file_required" });
      expect(f.requests).toEqual([]);
      expect(readUploadCheckpoints().requestIds).toBeUndefined();
    },
  );

  it("requires an unpredictable request UUID before writing a create checkpoint", async () => {
    const f = await fixture({ file: fakePdf(20) });
    vi.stubGlobal("crypto", { subtle: webcrypto.subtle });
    await expect(
      new ResumableSourceUpload({ api: f.api, file: f.file, metadata }).run(
        runOptions,
      ),
    ).rejects.toMatchObject({ code: "upload_crypto_unavailable" });
    expect(f.requests).toEqual([]);
    expect(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)).toBeNull();
  });

  it("does not write to an existing upload when the reselected PDF has a different length", async () => {
    const f = await fixture({ file: fakePdf(20) });
    const reselected = fakePdf(21);
    await expect(
      new ResumableSourceUpload({
        api: f.api,
        file: reselected,
        uploadId: id,
      }).run(runOptions),
    ).rejects.toMatchObject({ code: "upload_file_mismatch" });
    expect(f.requests.map((request) => request.method)).toEqual(["GET"]);
    expect(reselected.slice).not.toHaveBeenCalled();
  });

  it("requires a reselected PDF for a still-uploading session, without automatically recreating it", async () => {
    const f = await fixture({ file: fakePdf(20) });
    const task = new ResumableSourceUpload({ api: f.api, uploadId: id });
    await expect(task.run(runOptions)).rejects.toMatchObject({
      code: "upload_file_required",
    });
    expect(task.uploadId).toBe(id);
    expect(f.requests.map((request) => request.method)).toEqual(["GET"]);
  });

  it.each([
    null,
    "error",
    {},
    { detail: null },
    { detail: "unavailable" },
    { detail: {} },
    { detail: { code: 403 } },
  ])("uses a safe fallback for an unexpected error body: %j", async (body) => {
    const f = await fixture({ file: fakePdf(20) });
    interceptResponses(f, () => Response.json(body, { status: 403 }));
    await expect(
      new ResumableSourceUpload({ api: f.api, uploadId: id }).run(runOptions),
    ).rejects.toMatchObject({ status: 403, code: "upload_request_failed" });
    expect(f.requests.map((request) => request.method)).toEqual(["GET"]);
  });

  it.each(["", "-1", "1.5", "1000000", "Wed, 21 Oct 2030 07:28:00 GMT"])(
    "does not interpret malformed or unbounded Retry-After %j as a delay",
    async (retryAfter) => {
      const f = await fixture({
        file: fakePdf(20),
        error: { status: 429, code: "rate_limit_exceeded", retryAfter },
      });
      await expect(
        new ResumableSourceUpload({ api: f.api, uploadId: id }).run(runOptions),
      ).rejects.toMatchObject({ status: 429, retryAfterSeconds: undefined });
      expect(f.requests).toHaveLength(1);
    },
  );

  it.each([0, 999999])(
    "preserves the bounded Retry-After value %s",
    async (seconds) => {
      const f = await fixture({
        file: fakePdf(20),
        error: {
          status: 429,
          code: "rate_limit_exceeded",
          retryAfter: String(seconds),
        },
      });
      await expect(
        new ResumableSourceUpload({ api: f.api, uploadId: id }).run(runOptions),
      ).rejects.toMatchObject({ status: 429, retryAfterSeconds: seconds });
      expect(f.requests).toHaveLength(1);
    },
  );

  it("rejects an empty successful status response rather than guessing progress", async () => {
    const f = await fixture({ file: fakePdf(20) });
    interceptResponses(f, () => new Response(null, { status: 204 }));
    await expect(
      new ResumableSourceUpload({ api: f.api, uploadId: id }).run(runOptions),
    ).rejects.toMatchObject({ status: 204, code: "upload_request_failed" });
    expect(f.requests).toHaveLength(1);
  });
});

describe("bounded upload backpressure", () => {
  it("honors Retry-After and continues the same bounded part when explicitly enabled", async () => {
    const f = await fixture({ file: fakePdf(20) });
    const serve = f.fetchMock.getMockImplementation()!;
    let deniedAt = 0;
    let retriedAt = 0;
    const progress = vi.fn();
    f.fetchMock.mockImplementation(async (input, init) => {
      const request =
        input instanceof Request ? input : new Request(input, init);
      if (request.method === "PUT") {
        if (!deniedAt) {
          deniedAt = Date.now();
          return httpFailure(429, "rate_limit_exceeded", {
            "Retry-After": "1",
          });
        }
        retriedAt = Date.now();
      }
      return serve(request);
    });
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    const completed = await task.run({
      ...runOptions,
      retryRateLimits: true,
      onProgress: progress,
    });
    expect(completed.status).toBe("completed");
    expect(retriedAt - deniedAt).toBeGreaterThanOrEqual(1000);
    expect(progress).toHaveBeenCalledWith(
      expect.objectContaining({
        phase: "waiting",
        uploadedBytes: 0,
        totalBytes: 20,
        retryAfterSeconds: 1,
      }),
    );
    expect(f.requests.filter((request) => request.path === root)).toHaveLength(
      1,
    );
    expect(
      f.requests.filter((request) => request.method === "PUT"),
    ).toHaveLength(1);
    expect(f.file.arrayBuffer).not.toHaveBeenCalled();
  });

  it.each(["", "-1", "1.5", "181", "999999"])(
    "keeps manual recovery for an absent, invalid or excessive delay %j",
    async (retryAfter) => {
      const f = await fixture({
        error: { status: 429, code: "rate_limit_exceeded", retryAfter },
      });
      const task = new ResumableSourceUpload({ api: f.api, uploadId: id });
      await expect(
        task.run({ ...runOptions, retryRateLimits: true }),
      ).rejects.toMatchObject({ status: 429 });
      expect(f.requests).toHaveLength(1);
    },
  );

  it.each([401, 403, 409, 413, 422, 500])(
    "does not retry other HTTP %s responses when backpressure recovery is enabled",
    async (status) => {
      const f = await fixture({
        error: { status, code: "request_failed", retryAfter: "1" },
      });
      await expect(
        new ResumableSourceUpload({ api: f.api, uploadId: id }).run({
          ...runOptions,
          retryRateLimits: true,
        }),
      ).rejects.toMatchObject({ status });
      expect(f.requests).toHaveLength(1);
    },
  );

  it("waits before completion retry while preserving its exact expected version", async () => {
    const f = await fixture({ file: fakePdf(20) });
    const serve = f.fetchMock.getMockImplementation()!;
    const bodies: unknown[] = [];
    f.fetchMock.mockImplementation(async (input, init) => {
      const request =
        input instanceof Request ? input : new Request(input, init);
      if (
        request.method === "POST" &&
        new URL(request.url).pathname.endsWith("/complete")
      ) {
        bodies.push(await request.clone().json());
        if (bodies.length === 1)
          return httpFailure(429, "rate_limit_exceeded", {
            "Retry-After": "1",
          });
      }
      return serve(request);
    });
    const result = await new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    }).run({ ...runOptions, retryRateLimits: true });
    expect(result.status).toBe("completed");
    expect(bodies).toEqual([{ expected_version: 8 }, { expected_version: 8 }]);
    expect(
      f.requests.filter((request) => request.method === "PUT"),
    ).toHaveLength(1);
  });

  it("reconciles a part accepted before a rate-limited response without accepting mismatched progress", async () => {
    const f = await fixture({ file: fakePdf(20) });
    const serve = f.fetchMock.getMockImplementation()!;
    let puts = 0;
    f.fetchMock.mockImplementation(async (input, init) => {
      const request =
        input instanceof Request ? input : new Request(input, init);
      if (request.method === "PUT") {
        puts += 1;
        if (puts === 1) {
          await serve(request);
          return httpFailure(429, "rate_limit_exceeded", {
            "Retry-After": "1",
          });
        }
        return httpFailure(409, "source_upload_offset_conflict");
      }
      return serve(request);
    });
    const result = await new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    }).run({ ...runOptions, retryRateLimits: true });
    expect(result.status).toBe("completed");
    expect(puts).toBe(2);
    expect(
      f.requests.filter((request) => request.method === "PUT"),
    ).toHaveLength(1);
    expect(
      f.requests.some(
        (request) =>
          request.method === "GET" && request.path.endsWith("/chunks"),
      ),
    ).toBe(true);
  });

  it("does not automatically recreate an unacknowledged upload on rate limiting", async () => {
    const f = await fixture({
      file: fakePdf(20),
      error: { status: 429, code: "rate_limit_exceeded", retryAfter: "1" },
    });
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    await expect(
      task.run({ ...runOptions, retryRateLimits: true }),
    ).rejects.toMatchObject({ status: 429 });
    expect(f.requests).toHaveLength(1);
    expect(readUploadCheckpoints().requestIds).toEqual([task.requestId]);
  });

  it.each([
    ["0", 4, 3000],
    ["60", 4, 180000],
    ["61", 3, 122000],
  ] as const)(
    "bounds repeated rate limits with Retry-After %s by retries and total wait",
    async (retryAfter, requests, waitedMs) => {
      const f = await fixture({
        error: { status: 429, code: "rate_limit_exceeded", retryAfter },
      });
      vi.useFakeTimers();
      const start = Date.now();
      const result = new ResumableSourceUpload({ api: f.api, uploadId: id })
        .run({ ...runOptions, retryRateLimits: true })
        .catch((error: unknown) => error);
      await vi.runAllTimersAsync();
      expect(await result).toMatchObject({ status: 429 });
      expect(f.requests).toHaveLength(requests);
      expect(Date.now() - start).toBe(waitedMs);
    },
  );

  it.each([NaN, -1, Infinity])(
    "refuses an invalid injected retry duration %s",
    async (seconds) => {
      const f = await fixture();
      f.fetchMock.mockRejectedValue(
        new UploadFailure("rate_limit_exceeded", 429, seconds),
      );
      await expect(
        new ResumableSourceUpload({ api: f.api, uploadId: id }).run({
          ...runOptions,
          retryRateLimits: true,
        }),
      ).rejects.toMatchObject({ status: 429 });
      expect(f.fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it("pauses immediately while waiting and does not send a delayed retry", async () => {
    const f = await fixture({
      error: { status: 429, code: "rate_limit_exceeded", retryAfter: "60" },
    });
    vi.useFakeTimers();
    const controller = new AbortController();
    const progress = vi.fn();
    const task = new ResumableSourceUpload({ api: f.api, uploadId: id });
    const result = task
      .run({
        ...runOptions,
        retryRateLimits: true,
        signal: controller.signal,
        onProgress: progress,
      })
      .catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(100);
    expect(progress).toHaveBeenCalledWith(
      expect.objectContaining({ phase: "waiting" }),
    );
    controller.abort();
    expect(await result).toMatchObject({ code: "upload_paused" });
    await vi.runAllTimersAsync();
    expect(f.requests).toHaveLength(1);
    expect(task.uploadId).toBe(id);
  });

  it("resets the wait budget after acknowledged progress instead of limiting document length", async () => {
    const options = {
      file: fakePdf(20),
      prefix: 20,
      status: "pending" as const,
      neverComplete: true,
    };
    const f = await fixture(options);
    const serve = f.fetchMock.getMockImplementation()!;
    let attempts = 0;
    f.fetchMock.mockImplementation(async (input, init) => {
      attempts += 1;
      if (attempts % 4 !== 0)
        return httpFailure(429, "rate_limit_exceeded", { "Retry-After": "60" });
      options.neverComplete = attempts === 4;
      return serve(input, init);
    });
    vi.useFakeTimers();
    const result = new ResumableSourceUpload({ api: f.api, uploadId: id }).run({
      ...runOptions,
      retryRateLimits: true,
    });
    await vi.runAllTimersAsync();
    expect((await result).status).toBe("completed");
    expect(attempts).toBe(8);
    expect(f.requests.map((request) => request.method)).toEqual(["GET", "GET"]);
  });
});

describe("upload recovery and cancellation", () => {
  it("preserves a request checkpoint across a failed lookup and resolves it only after a successful GET", async () => {
    const f = await fixture({ file: fakePdf(20), requestId });
    recordUploadRequest(requestId);
    const saved = localStorage.getItem(UPLOAD_CHECKPOINT_KEY);
    f.fetchMock.mockRejectedValueOnce(new TypeError("network unavailable"));
    await expect(recoverUploadCheckpoints(f.api)).rejects.toMatchObject({
      code: "upload_interrupted",
    });
    expect(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)).toBe(saved);
    expect(f.fetchMock).toHaveBeenCalledTimes(1);
    await expect(recoverUploadCheckpoints(f.api)).resolves.toEqual({
      uploadIds: [id],
      creationUncertain: false,
    });
    expect(f.requests.map((request) => request.method)).toEqual(["GET"]);
  });

  it("does not treat a different 404 error as proof that an ambiguous create never happened", async () => {
    const f = await fixture({
      error: { status: 404, code: "account_unavailable" },
    });
    recordUploadRequest(requestId);
    const saved = localStorage.getItem(UPLOAD_CHECKPOINT_KEY);
    await expect(recoverUploadCheckpoints(f.api)).rejects.toMatchObject({
      code: "account_unavailable",
      status: 404,
    });
    expect(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)).toBe(saved);
    expect(f.requests.map((request) => request.method)).toEqual(["GET"]);
  });

  it("keeps successful partial recovery when another saved request cannot be looked up", async () => {
    const f = await fixture({ file: fakePdf(20), requestId });
    recordUploadRequest(requestId);
    recordUploadRequest(readJobId);
    interceptResponses(f, (request, response) =>
      request.url.endsWith(readJobId)
        ? httpFailure(503, "lookup_unavailable")
        : response,
    );
    await expect(recoverUploadCheckpoints(f.api)).rejects.toMatchObject({
      status: 503,
    });
    expect(readUploadCheckpoints()).toEqual({
      uploadIds: [id],
      requestIds: [readJobId],
      creationUncertain: true,
    });
    expect(f.requests.map((request) => request.method)).toEqual(["GET", "GET"]);
  });

  it("normalizes a supplied UUID and reports unknown total size without storing browser metadata", async () => {
    const key = "01234567-89ab-cdef-0123-456789abcdef";
    const f = await fixture({
      file: fakePdf(20),
      prefix: 20,
      status: "pending",
      requestId: key,
    });
    const onProgress = vi.fn();
    const onCreating = vi.fn();
    const task = new ResumableSourceUpload({
      api: f.api,
      requestId: key.toUpperCase(),
    });
    await expect(
      task.run({ ...runOptions, onProgress, onCreating }),
    ).resolves.toMatchObject({ status: "completed" });
    expect(task.requestId).toBe(key);
    expect(onProgress.mock.calls[0][0]).toMatchObject({
      phase: "creating",
      totalBytes: 0,
      uploadedBytes: 0,
    });
    expect(onCreating.mock.calls).toEqual([[true], [false]]);
    expect(f.requests[0].path).toBe(`${root}/by-request/${key}`);
    expect(f.requests.every((request) => request.method === "GET")).toBe(true);
    expect(readUploadCheckpoints()).toEqual({
      uploadIds: [id],
      creationUncertain: false,
    });
  });

  it("recovers locally without a network request when no request identities are pending", async () => {
    const f = await fixture({ file: fakePdf(20) });
    localStorage.setItem(
      UPLOAD_CHECKPOINT_KEY,
      JSON.stringify({ uploadIds: [id], creationUncertain: false }),
    );
    await expect(recoverUploadCheckpoints(f.api)).resolves.toEqual({
      uploadIds: [id],
      creationUncertain: false,
    });
    expect(f.fetchMock).not.toHaveBeenCalled();
  });

  it("keeps the original request UUID if the create response belongs to a different request", async () => {
    const f = await fixture({ file: fakePdf(20) });
    let wrongResponse = true;
    interceptResponses(f, async (request, response) => {
      if (
        request.method === "POST" &&
        new URL(request.url).pathname === root &&
        wrongResponse
      ) {
        wrongResponse = false;
        return Response.json({
          ...(await response.json()),
          request_id: documentId,
        });
      }
      return response;
    });
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    await expect(task.run(runOptions)).rejects.toMatchObject({
      code: "upload_creation_unknown",
      safeToRetry: true,
    });
    const key = task.requestId;
    expect(task.uploadId).toBeUndefined();
    expect(readUploadCheckpoints().requestIds).toEqual([key]);
    expect(f.requests.map((request) => request.method)).toEqual(["POST"]);
    await task.run(runOptions);
    const creates = f.requests.filter(
      (request) => request.method === "POST" && request.path === root,
    );
    expect(creates).toHaveLength(2);
    expect((creates[0].json as RequestBody).request_id).toBe(key);
    expect(creates[1].json).toEqual(creates[0].json);
  });

  it("retains an acknowledged session and its write-ahead UUID if saving the receipt link fails", async () => {
    const f = await fixture({ file: fakePdf(20) });
    const setItem = Storage.prototype.setItem;
    let writes = 0;
    const storage = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(function (this: Storage, key, value) {
        if (++writes === 2)
          throw new DOMException("Quota exceeded", "QuotaExceededError");
        setItem.call(this, key, value);
      });
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    await expect(task.run(runOptions)).rejects.toMatchObject({
      code: "upload_checkpoint_unavailable",
      safeToRetry: false,
    });
    expect(task.uploadId).toBe(id);
    expect(readUploadCheckpoints()).toEqual({
      uploadIds: [],
      requestIds: [task.requestId],
      creationUncertain: true,
    });
    expect(f.requests.map((request) => request.method)).toEqual(["POST"]);
    storage.mockRestore();
    await recoverUploadCheckpoints(f.api);
    await task.run(runOptions);
    expect(
      f.requests.filter(
        (request) => request.method === "POST" && request.path === root,
      ),
    ).toHaveLength(1);
    expect(readUploadCheckpoints()).toEqual({
      uploadIds: [id],
      creationUncertain: false,
    });
  });

  it.each(["creation", "chunk", "completion"] as const)(
    "does not replay an accepted %s when a progress consumer throws",
    async (stage) => {
      const f = await fixture({ file: fakePdf(20) });
      const error = new Error("Progress view unavailable");
      const task = new ResumableSourceUpload({
        api: f.api,
        file: f.file,
        metadata,
      });
      await expect(
        task.run({
          ...runOptions,
          onSession: (session) => {
            if (
              (stage === "creation" && session.next_offset === 0) ||
              (stage === "chunk" &&
                session.status === "uploading" &&
                session.next_offset === 20) ||
              (stage === "completion" && session.status === "pending")
            )
              throw error;
          },
        }),
      ).rejects.toBe(error);
      expect(task.uploadId).toBe(id);
      expect(readUploadCheckpoints()).toEqual({
        uploadIds: [id],
        creationUncertain: false,
      });
      await task.run(runOptions);
      expect(
        f.requests.filter(
          (request) => request.method === "POST" && request.path === root,
        ),
      ).toHaveLength(1);
      expect(
        f.requests.filter((request) => request.method === "PUT"),
      ).toHaveLength(1);
      expect(
        f.requests.filter((request) => request.path.endsWith("/complete")),
      ).toHaveLength(1);
    },
  );

  it("rejects a concurrent run while leaving the first immutable upload intact", async () => {
    const f = await fixture({ file: fakePdf(20) });
    const gate = Promise.withResolvers<void>();
    interceptResponses(f, async (request, response) => {
      if (new URL(request.url).pathname === root) await gate.promise;
      return response;
    });
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    const first = task.run(runOptions);
    await vi.waitFor(() => expect(f.requests).toHaveLength(1));
    await expect(task.run(runOptions)).rejects.toMatchObject({
      code: "upload_in_progress",
    });
    gate.resolve();
    await expect(first).resolves.toMatchObject({ status: "completed" });
    expect(f.requests.filter((request) => request.path === root)).toHaveLength(
      1,
    );
  });

  it("does nothing for an already-aborted run and remains usable afterwards", async () => {
    const f = await fixture({ file: fakePdf(20) });
    const controller = new AbortController();
    controller.abort();
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    await expect(task.run({ signal: controller.signal })).rejects.toMatchObject(
      { code: "upload_paused" },
    );
    expect(f.requests).toEqual([]);
    expect(f.file.slice).not.toHaveBeenCalled();
    expect(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)).toBeNull();
    await expect(task.run(runOptions)).resolves.toMatchObject({
      status: "completed",
    });
  });

  it.each(["before request", "before response", "rejected request"] as const)(
    "keeps a lookup checkpoint if cancelled %s",
    async (stage) => {
      const f = await fixture({ file: fakePdf(20), requestId });
      recordUploadRequest(requestId);
      const saved = localStorage.getItem(UPLOAD_CHECKPOINT_KEY);
      const controller = new AbortController();
      if (stage === "before request") controller.abort();
      else
        f.fetchMock.mockImplementationOnce(async () => {
          controller.abort();
          if (stage === "rejected request")
            throw new DOMException("Aborted", "AbortError");
          return Response.json(f.getSession());
        });
      await expect(
        recoverUploadCheckpoints(f.api, controller.signal),
      ).rejects.toMatchObject({ code: "upload_paused" });
      expect(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)).toBe(saved);
      expect(f.fetchMock).toHaveBeenCalledTimes(
        stage === "before request" ? 0 : 1,
      );
    },
  );

  it("reconciles a chunk accepted while its request was being cancelled before writing anything again", async () => {
    const f = await fixture({ file: fakePdf(20) });
    const controller = new AbortController();
    interceptResponses(f, (request, response) => {
      if (request.method === "PUT") controller.abort();
      return response;
    });
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    await expect(
      task.run({ ...runOptions, signal: controller.signal }),
    ).rejects.toMatchObject({ code: "upload_paused" });
    expect(f.getSession().next_offset).toBe(20);
    expect(
      f.requests.some((request) => request.path.endsWith("/complete")),
    ).toBe(false);
    await task.run(runOptions);
    expect(
      f.requests.filter((request) => request.method === "PUT"),
    ).toHaveLength(1);
    expect(
      f.requests.filter(
        (request) =>
          request.method === "GET" && request.path.endsWith("/chunks"),
      ),
    ).toHaveLength(1);
  });

  it("honors cancellation that occurs during asynchronous hashing before issuing a PUT", async () => {
    const f = await fixture({ file: fakePdf(20) });
    const controller = new AbortController();
    const hash = webcrypto.subtle.digest.bind(webcrypto.subtle);
    const digestSpy = vi
      .spyOn(webcrypto.subtle, "digest")
      .mockImplementation(async (...args) => {
        const result = await hash(...args);
        controller.abort();
        return result;
      });
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    await expect(
      task.run({ ...runOptions, signal: controller.signal }),
    ).rejects.toMatchObject({ code: "upload_paused" });
    expect(f.requests.map((request) => request.method)).toEqual(["POST"]);
    expect(readUploadCheckpoints().uploadIds).toEqual([id]);
    digestSpy.mockRestore();
    await task.run(runOptions);
    expect(
      f.requests.filter((request) => request.method === "PUT"),
    ).toHaveLength(1);
  });

  it("fails safely if secure hashing becomes unavailable after the session was acknowledged", async () => {
    const f = await fixture({ file: fakePdf(20) });
    await expect(
      new ResumableSourceUpload({ api: f.api, file: f.file, metadata }).run({
        ...runOptions,
        onProgress: (progress) => {
          if (progress.phase === "uploading") vi.stubGlobal("crypto", {});
        },
      }),
    ).rejects.toMatchObject({ code: "upload_crypto_unavailable" });
    expect(f.requests.map((request) => request.method)).toEqual(["POST"]);
    expect(f.file.slice).not.toHaveBeenCalled();
    expect(readUploadCheckpoints().uploadIds).toEqual([id]);
  });

  it("rejects a truncated File slice without hashing or sending those bytes", async () => {
    const f = await fixture({ file: fakePdf(20) });
    vi.mocked(f.file.slice).mockReturnValue({
      arrayBuffer: async () => new ArrayBuffer(19),
    } as Blob);
    const hash = vi.spyOn(webcrypto.subtle, "digest");
    await expect(
      new ResumableSourceUpload({ api: f.api, file: f.file, metadata }).run(
        runOptions,
      ),
    ).rejects.toMatchObject({ code: "upload_file_mismatch" });
    expect(hash).not.toHaveBeenCalled();
    expect(f.requests.map((request) => request.method)).toEqual(["POST"]);
  });

  it("cleans up the finalization timer when paused, with no later status request", async () => {
    const f = await fixture({
      file: fakePdf(20),
      prefix: 20,
      status: "finalizing",
    });
    vi.useFakeTimers();
    const controller = new AbortController();
    const task = new ResumableSourceUpload({ api: f.api, uploadId: id });
    const result = task
      .run({ signal: controller.signal })
      .catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(0);
    expect(vi.getTimerCount()).toBe(1);
    controller.abort();
    expect(await result).toMatchObject({ code: "upload_paused" });
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(f.requests).toHaveLength(1);
    f.getSession().status = "pending";
    await expect(task.run(runOptions)).resolves.toMatchObject({
      status: "completed",
    });
  });

  it("uses the default one-second interval and stops after 120 finalization polls", async () => {
    const f = await fixture({
      file: fakePdf(20),
      prefix: 20,
      status: "finalizing",
    });
    vi.useFakeTimers();
    const task = new ResumableSourceUpload({ api: f.api, uploadId: id });
    const result = task.run().catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(999);
    expect(f.requests).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(f.requests).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(119_000);
    expect(await result).toMatchObject({ code: "upload_still_finishing" });
    expect(f.requests).toHaveLength(121);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    [-1, 1],
    [1000, 600],
  ])(
    "bounds a requested poll budget of %s to %s status polls",
    async (maxPolls, expectedPolls) => {
      const f = await fixture({
        file: fakePdf(20),
        prefix: 20,
        status: "finalizing",
      });
      vi.useFakeTimers();
      const result = new ResumableSourceUpload({ api: f.api, uploadId: id })
        .run({ pollIntervalMs: -1, maxPolls })
        .catch((error: unknown) => error);
      await vi.advanceTimersByTimeAsync(0);
      await vi.runAllTimersAsync();
      expect(await result).toMatchObject({ code: "upload_still_finishing" });
      expect(f.requests).toHaveLength(expectedPolls + 1);
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it("caps a finalization delay at thirty seconds and removes the abort listener after the timer runs", async () => {
    const f = await fixture({
      file: fakePdf(20),
      prefix: 20,
      status: "finalizing",
    });
    vi.useFakeTimers();
    const controller = new AbortController();
    const remove = vi.spyOn(controller.signal, "removeEventListener");
    const result = new ResumableSourceUpload({ api: f.api, uploadId: id }).run({
      maxPolls: 1,
      pollIntervalMs: 90_000,
      signal: controller.signal,
    });
    await vi.advanceTimersByTimeAsync(29_999);
    expect(f.requests).toHaveLength(1);
    f.getSession().status = "pending";
    await vi.advanceTimersByTimeAsync(1);
    await expect(result).resolves.toMatchObject({ status: "completed" });
    expect(remove).toHaveBeenCalledWith("abort", expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("receipt and finalization integrity", () => {
  const badPrefixes: Array<{
    name: string;
    change: (page: ReceiptPage) => unknown;
  }> = [
    {
      name: "a receipt page for another upload",
      change: (page) => ({ ...page, upload_id: documentId }),
    },
    {
      name: "a changed acknowledged prefix",
      change: (page) => ({ ...page, next_offset: chunkBytes }),
    },
    { name: "missing receipts", change: (page) => ({ ...page, receipts: [] }) },
    {
      name: "an oversized receipt page",
      change: (page) => ({
        ...page,
        receipts: Array(65).fill(page.receipts[0]),
      }),
    },
    {
      name: "a gap before the first receipt",
      change: (page) => ({
        ...page,
        receipts: [{ ...page.receipts[0], offset: 1 }],
      }),
    },
    {
      name: "a short prefix receipt",
      change: (page) => ({
        ...page,
        receipts: [{ ...page.receipts[0], size_bytes: chunkBytes - 1 }],
      }),
    },
    {
      name: "a receipt beyond the acknowledged prefix",
      change: (page) => ({
        ...page,
        receipts: [
          ...page.receipts,
          {
            offset: chunkBytes * 2,
            size_bytes: 13,
            checksum_sha256: "a".repeat(64),
          },
        ],
      }),
    },
    {
      name: "a malformed checksum",
      change: (page) => ({
        ...page,
        receipts: [{ ...page.receipts[0], checksum_sha256: "not-a-hash" }],
      }),
    },
    {
      name: "a missing continuation cursor",
      change: (page) => ({
        ...page,
        receipts: [page.receipts[0]],
        next_receipt_offset: null,
      }),
    },
    {
      name: "a cursor skipping unverified bytes",
      change: (page) => ({
        ...page,
        receipts: [page.receipts[0]],
        next_receipt_offset: chunkBytes * 2,
      }),
    },
    {
      name: "an unexpected terminal continuation",
      change: (page) => ({ ...page, next_receipt_offset: chunkBytes * 2 }),
    },
  ];
  it.each(badPrefixes)(
    "rejects $name before writing anything from a reselected PDF",
    async ({ change }) => {
      const f = await fixture({ prefix: chunkBytes * 2 });
      const reselected = fakePdf(f.file.size);
      interceptResponses(f, async (request, response) =>
        request.method === "GET" &&
        new URL(request.url).pathname.endsWith("/chunks")
          ? Response.json(change((await response.json()) as ReceiptPage))
          : response,
      );
      await expect(
        new ResumableSourceUpload({
          api: f.api,
          file: reselected,
          uploadId: id,
        }).run(runOptions),
      ).rejects.toMatchObject({ code: "upload_receipts_invalid" });
      expect(f.requests.every((request) => request.method === "GET")).toBe(
        true,
      );
      expect(reselected.arrayBuffer).not.toHaveBeenCalled();
    },
  );

  const badReconciliations: Array<{
    name: string;
    change: (page: ReceiptPage) => unknown;
  }> = [
    {
      name: "another upload's receipt",
      change: (page) => ({ ...page, upload_id: documentId }),
    },
    {
      name: "progress changing between status and receipts",
      change: (page) => ({ ...page, next_offset: 21 }),
    },
    {
      name: "no accepted receipt",
      change: (page) => ({ ...page, receipts: [] }),
    },
    {
      name: "multiple conflicting receipts",
      change: (page) => ({
        ...page,
        receipts: [...page.receipts, page.receipts[0]],
      }),
    },
    {
      name: "a null receipt",
      change: (page) => ({ ...page, receipts: [null] }),
    },
    {
      name: "a wrong receipt offset",
      change: (page) => ({
        ...page,
        receipts: [{ ...page.receipts[0], offset: 1 }],
      }),
    },
    {
      name: "a wrong receipt length",
      change: (page) => ({
        ...page,
        receipts: [{ ...page.receipts[0], size_bytes: 19 }],
      }),
    },
    {
      name: "different acknowledged bytes",
      change: (page) => ({
        ...page,
        receipts: [{ ...page.receipts[0], checksum_sha256: "b".repeat(64) }],
      }),
    },
  ];
  it.each(badReconciliations)(
    "stops recovery on $name without replaying or completing",
    async ({ change }) => {
      const f = await fixture({ file: fakePdf(20) });
      interceptResponses(f, async (request, response) => {
        if (request.method === "PUT")
          return httpFailure(503, "response_unavailable");
        if (new URL(request.url).pathname.endsWith("/chunks"))
          return Response.json(change((await response.json()) as ReceiptPage));
        return response;
      });
      const task = new ResumableSourceUpload({
        api: f.api,
        file: f.file,
        metadata,
      });
      await expect(task.run(runOptions)).rejects.toMatchObject({
        code: "upload_receipts_invalid",
      });
      expect(f.requests.map((request) => request.method)).toEqual([
        "POST",
        "PUT",
        "GET",
        "GET",
      ]);
      expect(f.getSession().next_offset).toBe(20);
      expect(readUploadCheckpoints().uploadIds).toEqual([id]);
    },
  );

  it.each([
    [409, "source_upload_offset_conflict"],
    [503, "response_unavailable"],
  ])(
    "reconciles an accepted chunk after HTTP %s using its hash receipt, without replay",
    async (status, code) => {
      const f = await fixture({ file: fakePdf(20) });
      interceptResponses(f, (request, response) =>
        request.method === "PUT" ? httpFailure(status, code) : response,
      );
      await expect(
        new ResumableSourceUpload({ api: f.api, file: f.file, metadata }).run(
          runOptions,
        ),
      ).resolves.toMatchObject({ status: "completed" });
      expect(
        f.requests.filter((request) => request.method === "PUT"),
      ).toHaveLength(1);
      expect(
        f.requests.find(
          (request) =>
            request.method === "GET" && request.path.endsWith("/chunks"),
        ),
      ).toMatchObject({ offset: "0" });
    },
  );

  it("stops after three automatic accepted-chunk reconciliations, then safely resumes the same session", async () => {
    const f = await fixture({ file: fakePdf(chunkBytes * 4 + 13) });
    let failures = 0;
    interceptResponses(f, (request, response) =>
      request.method === "PUT" && failures++ < 4
        ? httpFailure(503, "response_unavailable")
        : response,
    );
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    await expect(task.run(runOptions)).rejects.toMatchObject({ status: 503 });
    expect(
      f.requests.filter((request) => request.method === "PUT"),
    ).toHaveLength(4);
    expect(
      f.requests.filter(
        (request) =>
          request.method === "GET" && request.path.endsWith("/chunks"),
      ),
    ).toHaveLength(3);
    expect(
      f.requests.some((request) => request.path.endsWith("/complete")),
    ).toBe(false);
    await task.run(runOptions);
    expect(
      f.requests
        .filter((request) => request.method === "PUT")
        .map((request) => Number(request.offset)),
    ).toEqual([0, chunkBytes, chunkBytes * 2, chunkBytes * 3, chunkBytes * 4]);
    expect(f.requests.filter((request) => request.path === root)).toHaveLength(
      1,
    );
  });

  it("does not accept a non-contiguous successful chunk acknowledgement", async () => {
    const f = await fixture({ file: fakePdf(20) });
    interceptResponses(f, async (request, response) =>
      request.method === "PUT"
        ? Response.json({ ...(await response.json()), next_offset: 0 })
        : response,
    );
    await expect(
      new ResumableSourceUpload({ api: f.api, file: f.file, metadata }).run(
        runOptions,
      ),
    ).rejects.toMatchObject({ code: "upload_changed" });
    expect(f.requests.map((request) => request.method)).toEqual([
      "POST",
      "PUT",
    ]);
    expect(readUploadCheckpoints().uploadIds).toEqual([id]);
  });

  it.each([
    [401, "authentication_required"],
    [403, "permission_denied"],
    [409, "source_upload_quota_exceeded"],
    [429, "rate_limit_exceeded"],
  ])(
    "stops a chunk on HTTP %s rather than automatically retrying",
    async (status, code) => {
      const f = await fixture({
        file: fakePdf(20),
        error: { status, code, method: "PUT", retryAfter: "2" },
      });
      const task = new ResumableSourceUpload({
        api: f.api,
        file: f.file,
        metadata,
      });
      await expect(task.run(runOptions)).rejects.toMatchObject({
        status,
        code,
        retryAfterSeconds: 2,
      });
      expect(f.requests.map((request) => request.method)).toEqual([
        "POST",
        "PUT",
      ]);
      expect(task.uploadId).toBe(id);
      expect(f.getSession().next_offset).toBe(0);
    },
  );

  it.each([
    [401, "authentication_required"],
    [403, "permission_denied"],
    [409, "source_upload_version_conflict"],
    [422, "invalid_upload"],
    [429, "rate_limit_exceeded"],
  ])(
    "preserves a completion HTTP %s error without auto-polling or resubmitting",
    async (status, code) => {
      const f = await fixture({
        file: fakePdf(20),
        prefix: 20,
        error: { status, code, method: "POST" },
      });
      const task = new ResumableSourceUpload({ api: f.api, uploadId: id });
      await expect(task.run(runOptions)).rejects.toMatchObject({
        status,
        code,
      });
      expect(f.requests.map((request) => request.method)).toEqual([
        "GET",
        "POST",
      ]);
      expect(task.uploadId).toBe(id);
    },
  );

  it("does not repeat completion after a transient failure with no accepted finalization", async () => {
    const f = await fixture({
      file: fakePdf(20),
      prefix: 20,
      error: { status: 503, code: "dispatcher_unavailable", method: "POST" },
    });
    await expect(
      new ResumableSourceUpload({ api: f.api, uploadId: id }).run(runOptions),
    ).rejects.toMatchObject({ code: "upload_interrupted" });
    expect(f.requests.map((request) => request.method)).toEqual([
      "GET",
      "POST",
      "GET",
    ]);
  });

  it("reconciles completion accepted before a server error through GET only", async () => {
    const f = await fixture({ file: fakePdf(20), prefix: 20 });
    interceptResponses(f, (request, response) =>
      request.method === "POST"
        ? httpFailure(503, "response_unavailable")
        : response,
    );
    await expect(
      new ResumableSourceUpload({ api: f.api, uploadId: id }).run(runOptions),
    ).resolves.toMatchObject({ status: "completed" });
    expect(f.requests.map((request) => request.method)).toEqual([
      "GET",
      "POST",
      "GET",
    ]);
  });

  it("rejects a completion response that leaves the upload in an impossible non-finalizing state", async () => {
    const f = await fixture({ file: fakePdf(20), prefix: 20 });
    interceptResponses(f, async (request, response) =>
      request.method === "POST"
        ? Response.json({ ...(await response.json()), status: "uploading" })
        : response,
    );
    const task = new ResumableSourceUpload({ api: f.api, uploadId: id });
    await expect(task.run(runOptions)).rejects.toMatchObject({
      code: "upload_response_invalid",
    });
    expect(f.requests.map((request) => request.method)).toEqual([
      "GET",
      "POST",
    ]);
    await expect(task.run(runOptions)).resolves.toMatchObject({
      status: "completed",
    });
    expect(
      f.requests.filter((request) => request.path.endsWith("/complete")),
    ).toHaveLength(1);
  });

  it.each(["initial status", "during finalization"] as const)(
    "reports a failed upload from %s without any automatic restart",
    async (stage) => {
      const f = await fixture({
        file: fakePdf(20),
        prefix: 20,
        status: stage === "initial status" ? "failed" : "finalizing",
      });
      let polls = 0;
      interceptResponses(f, async (_request, response) =>
        ++polls > 1
          ? Response.json({ ...(await response.json()), status: "failed" })
          : response,
      );
      const task = new ResumableSourceUpload({ api: f.api, uploadId: id });
      await expect(task.run(runOptions)).rejects.toMatchObject({
        code: "upload_failed",
      });
      expect(f.requests).toHaveLength(stage === "initial status" ? 1 : 2);
      expect(f.requests.every((request) => request.method === "GET")).toBe(
        true,
      );
      expect(task.uploadId).toBe(id);
    },
  );

  it.each(["offset", "size"] as const)(
    "rejects a changed server %s on resume despite a newer version",
    async (field) => {
      const f = await fixture();
      const controller = new AbortController();
      const task = new ResumableSourceUpload({
        api: f.api,
        file: f.file,
        metadata,
      });
      await expect(
        task.run({
          ...runOptions,
          signal: controller.signal,
          onProgress: (progress) => {
            if (progress.uploadedBytes === chunkBytes) controller.abort();
          },
        }),
      ).rejects.toMatchObject({ code: "upload_paused" });
      f.getSession().version += 1;
      if (field === "offset") f.getSession().next_offset = 0;
      else f.getSession().size_bytes += 1;
      const count = f.requests.length;
      await expect(task.run(runOptions)).rejects.toMatchObject({
        code: "upload_changed",
      });
      expect(f.requests.slice(count).map((request) => request.method)).toEqual([
        "GET",
      ]);
    },
  );
});

describe("low-level upload safety guards", () => {
  // These intentionally redundant guards protect internal readers even when no public run can reach them with valid inputs.
  it("refuses to read a chunk without a File, without accessing crypto or transport", async () => {
    const f = await fixture({ file: fakePdf(20) });
    const hash = vi.spyOn(webcrypto.subtle, "digest");
    const task = new ResumableSourceUpload({ api: f.api, uploadId: id });
    await expect(task["chunk"](0, 20, {})).rejects.toMatchObject({
      code: "upload_file_required",
    });
    expect(hash).not.toHaveBeenCalled();
    expect(f.fetchMock).not.toHaveBeenCalled();
    expect(f.file.slice).not.toHaveBeenCalled();
    expect(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)).toBeNull();
  });

  it.each([false, true])(
    "also rejects invalid File identity at the prefix-verifier boundary (File present: %s)",
    async (hasFile) => {
      const f = await fixture({ file: fakePdf(20) });
      const reselected = hasFile ? fakePdf(21) : undefined;
      const task = new ResumableSourceUpload({
        api: f.api,
        uploadId: id,
        file: reselected,
      });
      // Establish a real server snapshot through the public flow; neither layer may verify or write a different/missing file.
      await expect(task.run(runOptions)).rejects.toMatchObject({
        code: hasFile ? "upload_file_mismatch" : "upload_file_required",
      });
      await expect(task["verifyPrefix"]({})).rejects.toMatchObject({
        code: "upload_file_mismatch",
      });
      expect(f.requests.map((request) => request.method)).toEqual(["GET"]);
      if (reselected) expect(reselected.slice).not.toHaveBeenCalled();
    },
  );

  it("retains the same creation identity after an unexpected post-response processing exception", async () => {
    const f = await fixture({ file: fakePdf(20) });
    let decoderFailed = false;
    interceptResponses(f, async (request, response) => {
      if (
        request.method === "POST" &&
        new URL(request.url).pathname === root &&
        !decoderFailed
      ) {
        decoderFailed = true;
        const decoded = (await response.clone().json()) as Session;
        response.headers.set(
          "Content-Length",
          String(new TextEncoder().encode(JSON.stringify(decoded)).byteLength),
        );
        Object.defineProperty(decoded, "version", {
          get: () => {
            throw new Error("Response cache could not read the version");
          },
        });
        // Fault injection at the service-client response boundary, after the server has accepted the request.
        vi.spyOn(response, "json").mockResolvedValueOnce(decoded);
      }
      return response;
    });
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    await expect(task.run(runOptions)).rejects.toMatchObject({
      code: "upload_creation_unknown",
      status: 0,
      safeToRetry: true,
    });
    expect(task.uploadId).toBeUndefined();
    expect(readUploadCheckpoints()).toEqual({
      uploadIds: [],
      requestIds: [task.requestId],
      creationUncertain: true,
    });
    expect(f.file.slice).not.toHaveBeenCalled();
    expect(f.requests.map((request) => request.method)).toEqual(["POST"]);
    await expect(task.run(runOptions)).resolves.toMatchObject({
      status: "completed",
      id,
    });
    const creates = f.requests.filter((request) => request.path === root);
    expect(creates).toHaveLength(2);
    expect(creates[1].json).toEqual(creates[0].json);
    expect(readUploadCheckpoints()).toEqual({
      uploadIds: [id],
      creationUncertain: false,
    });
  });
});

describe("bounded resumable source uploads", () => {
  it("checks secure hashing support before reserving any upload capacity", async () => {
    const f = await fixture();
    vi.stubGlobal("crypto", {});
    await expect(
      new ResumableSourceUpload({ api: f.api, file: f.file, metadata }).run(
        runOptions,
      ),
    ).rejects.toMatchObject({ code: "upload_crypto_unavailable" });
    expect(f.requests).toEqual([]);
  });

  it("rejects regressed server versions on resume instead of treating them as current", async () => {
    const f = await fixture();
    const controller = new AbortController();
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    await expect(
      task.run({
        ...runOptions,
        signal: controller.signal,
        onProgress: (progress) => {
          if (progress.uploadedBytes === chunkBytes) controller.abort();
        },
      }),
    ).rejects.toMatchObject({ code: "upload_paused" });
    const count = f.requests.length;
    f.fetchMock.mockImplementationOnce(async () =>
      Response.json({ ...f.getSession(), version: 1 }),
    );
    await expect(task.run(runOptions)).rejects.toMatchObject({
      code: "upload_changed",
    });
    expect(f.requests).toHaveLength(count);
  });

  it("explains unavailable or corrupt browser checkpoints without claiming that progress was saved", () => {
    expect(
      uploadFailureMessage(new UploadFailure("upload_checkpoint_unavailable")),
    ).toContain("browser cannot save");
    expect(
      uploadFailureMessage(new UploadFailure("upload_checkpoint_invalid")),
    ).toContain("recovery links could not be read");
  });

  it("uploads a PDF larger than 256 MiB using exact 4 MiB slices and per-chunk SHA-256, with no second reading request", async () => {
    const f = await fixture({ file: fakePdf(chunkBytes * 65 + 13) });
    const progress: number[] = [];
    const checkpoints: string[] = [];
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    const completed = await task.run({
      ...runOptions,
      onProgress: (state) => progress.push(state.uploadedBytes),
      onSession: (session) => checkpoints.push(session.id),
    });
    expect(UPLOAD_CHUNK_BYTES).toBe(chunkBytes);
    expect(f.file.arrayBuffer).not.toHaveBeenCalled();
    const puts = f.requests.filter((request) => request.method === "PUT");
    expect(puts).toHaveLength(66);
    expect(puts.at(-1)?.bytes).toBe(13);
    expect(puts.map((request) => Number(request.offset))).toEqual(
      Array.from({ length: 66 }, (_, index) => index * chunkBytes),
    );
    expect(
      f.requests.find((request) => request.method === "POST")?.json,
    ).toEqual({
      ...metadata,
      filename: f.file.name,
      size_bytes: f.file.size,
      request_id: expect.any(String),
    });
    expect(completed).toMatchObject({
      document_id: documentId,
      source_read_job_id: readJobId,
      status: "completed",
    });
    expect(checkpoints[0]).toBe(id);
    expect(Math.max(...progress)).toBe(f.file.size);
    expect(
      f.requests.every((request) => request.path.includes("/source-uploads")),
    ).toBe(true);
  });

  it("verifies all persisted prefix receipts across 64-receipt pages before a reselected file writes anything", async () => {
    const original = fakePdf(chunkBytes * 66 + 13);
    const f = await fixture({ file: original, prefix: chunkBytes * 65 });
    const reselected = fakePdf(original.size);
    const task = new ResumableSourceUpload({
      api: f.api,
      file: reselected,
      uploadId: id,
    });
    await task.run(runOptions);
    const receiptRequests = f.requests.filter(
      (request) => request.method === "GET" && request.path.endsWith("/chunks"),
    );
    expect(receiptRequests.map((request) => request.offset)).toEqual([
      "0",
      String(chunkBytes * 64),
    ]);
    expect(
      f.requests
        .filter((request) => request.method === "PUT")
        .map((request) => Number(request.offset)),
    ).toEqual([chunkBytes * 65, chunkBytes * 66]);
    expect(reselected.arrayBuffer).not.toHaveBeenCalled();
    expect(reselected.slice).toHaveBeenCalledTimes(67);
    expect(
      f.requests.some(
        (request) =>
          request.method === "POST" && request.path.endsWith("/source-uploads"),
      ),
    ).toBe(false);
  });

  it("rejects a different later prefix chunk despite matching name, size, mtime and first chunk, without PUT or complete", async () => {
    const original = fakePdf(chunkBytes * 66);
    const f = await fixture({ file: original, prefix: chunkBytes * 65 });
    const replacement = fakePdf(original.size, 64);
    const task = new ResumableSourceUpload({
      api: f.api,
      file: replacement,
      uploadId: id,
    });
    await expect(task.run(runOptions)).rejects.toMatchObject({
      code: "upload_file_mismatch",
    });
    expect(f.requests.every((request) => request.method === "GET")).toBe(true);
    expect(replacement.slice).toHaveBeenCalledTimes(65);
  });

  it("fails closed when an acknowledged prefix has missing receipts", async () => {
    const f = await fixture({ prefix: chunkBytes, badReceipts: true });
    await expect(
      new ResumableSourceUpload({ api: f.api, file: f.file, uploadId: id }).run(
        runOptions,
      ),
    ).rejects.toMatchObject({ code: "upload_receipts_invalid" });
    expect(f.requests.every((request) => request.method === "GET")).toBe(true);
  });

  it("reconciles a lost accepted chunk using GET and its immutable receipt rather than resending it", async () => {
    const f = await fixture({ fault: "lost-chunk" });
    await new ResumableSourceUpload({ api: f.api, file: f.file, metadata }).run(
      runOptions,
    );
    const puts = f.requests.filter((request) => request.method === "PUT");
    expect(puts.map((request) => request.offset)).toEqual([
      "0",
      String(chunkBytes),
      String(chunkBytes * 2),
    ]);
    const firstPut = f.requests.findIndex(
      (request) => request.method === "PUT",
    );
    expect(f.requests[firstPut + 1]).toMatchObject({
      method: "GET",
      path: `/api/v1/admin/source-uploads/${id}`,
    });
    expect(f.requests[firstPut + 2]).toMatchObject({
      method: "GET",
      path: `/api/v1/admin/source-uploads/${id}/chunks`,
      offset: "0",
    });
  });

  it("pauses after an unaccepted chunk failure and resumes the immutable in-memory File from fresh server state", async () => {
    const f = await fixture({ fault: "chunk-down" });
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    await expect(task.run(runOptions)).rejects.toMatchObject({
      code: "upload_interrupted",
    });
    expect(task.uploadId).toBe(id);
    expect(
      f.requests.filter((request) => request.method === "PUT"),
    ).toHaveLength(1);
    expect(f.requests.at(-1)).toMatchObject({
      method: "GET",
      path: `/api/v1/admin/source-uploads/${id}`,
    });
    await task.run(runOptions);
    expect(
      f.requests.filter(
        (request) =>
          request.method === "POST" && request.path.endsWith("/source-uploads"),
      ),
    ).toHaveLength(1);
  });

  it("reconciles a lost completion response and polls the existing finalization without another complete request", async () => {
    const f = await fixture({ fault: "lost-complete" });
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    await task.run(runOptions);
    await task.run(runOptions);
    expect(
      f.requests.filter((request) => request.path.endsWith("/complete")),
    ).toHaveLength(1);
  });

  it("can recover a pending finalization after a browser restart without loading a File or creating another upload", async () => {
    const f = await fixture({
      file: fakePdf(200),
      prefix: 200,
      status: "pending",
      deduplicated: true,
    });
    const result = await new ResumableSourceUpload({
      api: f.api,
      uploadId: id,
    }).run(runOptions);
    expect(result.deduplicated).toBe(true);
    expect(f.requests.every((request) => request.method === "GET")).toBe(true);
  });

  it("cancellation preserves the acknowledged checkpoint; a new task verifies it before continuing", async () => {
    const f = await fixture();
    const signal = new AbortController();
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    await expect(
      task.run({
        ...runOptions,
        signal: signal.signal,
        onProgress: (progress) => {
          if (
            progress.phase === "uploading" &&
            progress.uploadedBytes === chunkBytes
          )
            signal.abort();
        },
      }),
    ).rejects.toMatchObject({ code: "upload_paused" });
    expect(task.uploadId).toBe(id);
    expect(f.getSession().next_offset).toBe(chunkBytes);
    await new ResumableSourceUpload({
      api: f.api,
      file: fakePdf(f.file.size),
      uploadId: id,
    }).run(runOptions);
    expect(
      f.requests.filter(
        (request) => request.method === "PUT" && request.offset === "0",
      ),
    ).toHaveLength(1);
  });

  it.each(["lost-create", "unaccepted-create"] as const)(
    "reuses the write-ahead request UUID after %s without manufacturing another session",
    async (fault) => {
      const f = await fixture({ fault });
      const creating = vi.fn(() => {
        expect(readUploadCheckpoints().requestIds).toEqual([task.requestId]);
      });
      const task = new ResumableSourceUpload({
        api: f.api,
        file: f.file,
        metadata,
      });
      await expect(
        task.run({
          ...runOptions,
          onCreating: (uncertain) => {
            if (uncertain) creating();
          },
        }),
      ).rejects.toMatchObject({
        code: "upload_creation_unknown",
        safeToRetry: true,
      });
      const requestId = task.requestId;
      expect(requestId).toMatch(/^[0-9a-f-]{36}$/);
      expect(readUploadCheckpoints().requestIds).toEqual([requestId]);
      await expect(task.run(runOptions)).resolves.toMatchObject({
        id,
        status: "completed",
      });
      const creates = f.requests.filter(
        (request) =>
          request.method === "POST" && request.path.endsWith("/source-uploads"),
      );
      expect(creates).toHaveLength(2);
      expect(creates[0].json).toEqual(creates[1].json);
      expect(creates[1].json).toMatchObject({ request_id: requestId });
      expect(readUploadCheckpoints()).toEqual({
        uploadIds: [id],
        creationUncertain: false,
      });
    },
  );

  it("recovers a lost accepted create after refresh using only the checkpoint UUID and GET", async () => {
    const f = await fixture({ fault: "lost-create" });
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    await expect(task.run(runOptions)).rejects.toMatchObject({
      code: "upload_creation_unknown",
    });
    const before = f.requests.length;
    expect(JSON.parse(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)!)).toEqual({
      uploadIds: [],
      creationUncertain: true,
      requestIds: [task.requestId],
    });
    expect(await recoverUploadCheckpoints(f.api)).toEqual({
      uploadIds: [id],
      creationUncertain: false,
    });
    expect(f.requests.slice(before)).toEqual([
      expect.objectContaining({
        method: "GET",
        path: `/api/v1/admin/source-uploads/by-request/${task.requestId}`,
      }),
    ]);
    await new ResumableSourceUpload({
      api: f.api,
      file: fakePdf(f.file.size),
      uploadId: id,
    }).run(runOptions);
    expect(
      f.requests.filter(
        (request) =>
          request.method === "POST" && request.path.endsWith("/source-uploads"),
      ),
    ).toHaveLength(1);
  });

  it("retains a not-yet-visible request on lookup 404 and later replays that same key", async () => {
    const f = await fixture({ fault: "unaccepted-create" });
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    await expect(task.run(runOptions)).rejects.toMatchObject({
      code: "upload_creation_unknown",
    });
    const recovered = await recoverUploadCheckpoints(f.api);
    expect(recovered.requestIds).toEqual([task.requestId]);
    expect(recovered.creationUncertain).toBe(true);
    const restarted = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
      requestId: task.requestId,
    });
    await restarted.run(runOptions);
    expect(
      f.requests
        .filter(
          (request) =>
            request.method === "POST" &&
            request.path.endsWith("/source-uploads"),
        )
        .map((request) => request.json),
    ).toEqual([
      expect.objectContaining({ request_id: task.requestId }),
      expect.objectContaining({ request_id: task.requestId }),
    ]);
  });

  it("does not POST if a request UUID cannot be persisted before transmission", async () => {
    const f = await fixture();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage unavailable");
    });
    await expect(
      new ResumableSourceUpload({ api: f.api, file: f.file, metadata }).run(
        runOptions,
      ),
    ).rejects.toMatchObject({ code: "upload_checkpoint_unavailable" });
    expect(f.requests).toEqual([]);
  });

  it("requires receipt verification when replaying a keyed create whose upload has already advanced", async () => {
    const key = "00000000-0000-0000-0000-000000000968";
    const f = await fixture({ requestId: key, prefix: chunkBytes * 2 });
    recordUploadRequest(key);
    await expect(
      new ResumableSourceUpload({
        api: f.api,
        file: fakePdf(f.file.size, 1),
        metadata,
        requestId: key,
      }).run(runOptions),
    ).rejects.toMatchObject({ code: "upload_file_mismatch" });
    expect(
      f.requests.some(
        (request) =>
          request.method === "PUT" || request.path.endsWith("/complete"),
      ),
    ).toBe(false);
  });

  it("can look up and poll an accepted request without a File or browser-stored metadata", async () => {
    const key = "00000000-0000-0000-0000-000000000969";
    const f = await fixture({
      requestId: key,
      file: fakePdf(20),
      prefix: 20,
      status: "pending",
    });
    recordUploadRequest(key);
    await expect(
      new ResumableSourceUpload({ api: f.api, requestId: key }).run(runOptions),
    ).resolves.toMatchObject({ id, status: "completed" });
    expect(f.requests.every((request) => request.method === "GET")).toBe(true);
  });

  it.each([401, 403, 500])(
    "preserves pending UUIDs on lookup %s without ever POSTing",
    async (status) => {
      const key = "00000000-0000-0000-0000-000000000970";
      const f = await fixture({
        error: { status, code: "lookup_unavailable" },
      });
      recordUploadRequest(key);
      const saved = localStorage.getItem(UPLOAD_CHECKPOINT_KEY);
      await expect(recoverUploadCheckpoints(f.api)).rejects.toMatchObject({
        status,
      });
      expect(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)).toBe(saved);
      expect(f.requests.map((request) => request.method)).toEqual(["GET"]);
    },
  );

  it("rejects a lookup that returns another request identity without replacing its checkpoint", async () => {
    const key = "00000000-0000-0000-0000-000000000970";
    const f = await fixture({ requestId: key });
    recordUploadRequest(key);
    f.fetchMock.mockImplementationOnce(async () =>
      Response.json({ ...f.getSession(), request_id: id }),
    );
    await expect(recoverUploadCheckpoints(f.api)).rejects.toMatchObject({
      code: "upload_response_invalid",
    });
    expect(readUploadCheckpoints().requestIds).toEqual([key]);
    expect(readUploadCheckpoints().uploadIds).toEqual([]);
  });

  it("refuses a fresh identity while another create is unresolved and never guesses from file metadata", async () => {
    const f = await fixture({ fault: "lost-create" });
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata,
    });
    await expect(task.run(runOptions)).rejects.toMatchObject({
      code: "upload_creation_unknown",
    });
    await expect(
      new ResumableSourceUpload({
        api: f.api,
        file: fakePdf(f.file.size),
        metadata,
      }).run(runOptions),
    ).rejects.toMatchObject({ code: "upload_creation_unknown" });
    expect(f.requests).toHaveLength(1);
    expect(readUploadCheckpoints().requestIds).toEqual([task.requestId]);
  });

  it("keeps the initial in-memory metadata payload stable when the caller edits its form after response loss", async () => {
    const f = await fixture({ fault: "unaccepted-create" });
    const values = { ...metadata };
    const task = new ResumableSourceUpload({
      api: f.api,
      file: f.file,
      metadata: values,
    });
    await expect(task.run(runOptions)).rejects.toMatchObject({
      code: "upload_creation_unknown",
    });
    values.year = 2024;
    await task.run(runOptions);
    const creates = f.requests.filter(
      (request) =>
        request.method === "POST" && request.path.endsWith("/source-uploads"),
    );
    expect(creates[0].json).toEqual(creates[1].json);
    expect(creates[1].json).toMatchObject({ year: 2025 });
  });

  it.each([
    [401, "authentication_required"],
    [403, "permission_denied"],
    [409, "source_upload_quota_exceeded"],
    [413, "source_upload_too_large"],
    [429, "rate_limit_exceeded"],
    [503, "source_upload_storage_unsupported"],
  ])(
    "surfaces %s upload failures without retry loops or invented capacity changes",
    async (status, code) => {
      const f = await fixture({
        error: { status, code, retryAfter: status === 429 ? "27" : undefined },
      });
      const task = new ResumableSourceUpload({
        api: f.api,
        file: f.file,
        metadata,
      });
      const failure = await task
        .run(runOptions)
        .catch((error: unknown) => error);
      expect(failure).toMatchObject({ status, code });
      if (status === 429)
        expect(failure).toHaveProperty("retryAfterSeconds", 27);
      expect(uploadFailureMessage(failure)).not.toContain(code);
      expect(f.requests).toHaveLength(1);
    },
  );

  it("bounds finalization polling and lets the operator check the same upload again", async () => {
    const f = await fixture({
      file: fakePdf(200),
      prefix: 200,
      status: "pending",
      neverComplete: true,
    });
    await expect(
      new ResumableSourceUpload({ api: f.api, uploadId: id }).run(runOptions),
    ).rejects.toMatchObject({ code: "upload_still_finishing" });
    expect(f.requests.length).toBeLessThanOrEqual(5);
    expect(f.requests.every((request) => request.method === "GET")).toBe(true);
  });
});
