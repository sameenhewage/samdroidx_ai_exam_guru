import type { ApiClient, components } from "@exam-guru/api-client";

import {
  readUploadCheckpoints,
  recordUploadRequest,
  resolveUploadRequest,
  type UploadCheckpoints,
} from "./source-upload-checkpoint";

export const UPLOAD_CHUNK_BYTES = 4_194_304;
export type UploadSession = components["schemas"]["SourceUploadResponse"];
export type UploadMetadata = Omit<
  components["schemas"]["SourceUploadCreateRequest"],
  "filename" | "size_bytes" | "request_id"
>;
type ReceiptPage = components["schemas"]["SourceUploadChunkPageResponse"];

export type UploadProgress = {
  phase: "creating" | "checking" | "uploading" | "finishing";
  uploadedBytes: number;
  totalBytes: number;
  checkedBytes?: number;
};
type RunOptions = {
  signal?: AbortSignal;
  onProgress?: (progress: UploadProgress) => void;
  onSession?: (session: UploadSession) => void;
  onCreating?: (uncertain: boolean) => void;
  pollIntervalMs?: number;
  maxPolls?: number;
};

export class UploadFailure extends Error {
  constructor(
    public readonly code: string,
    public readonly status = 0,
    public readonly retryAfterSeconds?: number,
    public readonly safeToRetry = true,
  ) {
    super(code);
    this.name = "UploadFailure";
  }
}

export function isUploadId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value)
  );
}

function failure(error: unknown, response: Response): UploadFailure {
  let code = "upload_request_failed";
  if (error && typeof error === "object" && "detail" in error) {
    const detail = error.detail;
    if (
      detail &&
      typeof detail === "object" &&
      "code" in detail &&
      typeof detail.code === "string"
    )
      code = detail.code;
  }
  const retry = response.headers.get("Retry-After");
  const seconds = retry && /^\d{1,6}$/.test(retry) ? Number(retry) : undefined;
  return new UploadFailure(code, response.status, seconds);
}

export function uploadFailureMessage(error: unknown): string {
  if (!(error instanceof UploadFailure))
    return "The upload was interrupted. Your saved progress has been kept. Try continuing the upload.";
  if (error.code === "upload_checkpoint_unavailable")
    return "This browser cannot save upload recovery links. Enable local storage before starting an upload; existing server progress is not deleted.";
  if (error.code === "upload_checkpoint_invalid")
    return "The saved upload recovery links could not be read. Ask an administrator to recover them before starting another upload.";
  if (error.code === "upload_paused")
    return "Upload paused. Your saved progress is kept; you can continue with the same PDF.";
  if (error.code === "upload_creation_unknown")
    return error.safeToRetry
      ? "The upload response was interrupted. Its recovery link is kept. Do not start it again as a new upload; continue the saved upload or recover its progress."
      : "Studio may have started this upload, but its saved link was not returned. Do not start it again: ask an administrator to recover the upload before retrying.";
  if (error.code === "source_upload_request_conflict")
    return "These details differ from the saved upload request. Recover the existing upload before trying again; its saved content has not been changed.";
  if (error.code === "upload_file_mismatch")
    return "This PDF does not match the saved upload. Choose the original PDF; nothing has been added to that upload.";
  if (error.code === "upload_file_required")
    return "Choose the original PDF to continue. Its saved content will be checked before any more is uploaded.";
  if (
    error.code === "upload_receipts_invalid" ||
    error.code === "upload_changed"
  )
    return "The saved upload could not be verified. Nothing more was uploaded. Refresh its progress and try again.";
  if (error.code === "upload_still_finishing")
    return "Studio is still finishing this upload. Progress is saved; check its status again shortly.";
  if (error.code === "upload_crypto_unavailable")
    return "This browser cannot securely check the PDF. Open Studio on localhost or a secure connection and try again.";
  if (error.code === "source_upload_quota_exceeded")
    return "Studio does not have upload capacity available for this account right now. Existing uploads and saved files are kept. Ask an administrator to review capacity before trying again.";
  if (error.code === "source_upload_storage_unsupported")
    return "Resumable upload is not available with this Studio storage configuration. Ask an administrator to enable supported local storage.";
  if (error.status === 401)
    return "Your session has expired. Sign in again, then continue the saved upload.";
  if (error.status === 403)
    return "Your account does not have permission to upload this PDF. Sign in with the original upload account.";
  if (error.status === 404)
    return "This saved upload is not available to this account. Sign in with its original account or ask an administrator for help.";
  if (error.status === 429)
    return error.retryAfterSeconds === undefined
      ? "Studio is receiving uploads too quickly. Your progress is kept; wait before continuing."
      : `Studio is receiving uploads too quickly. Your progress is kept; wait ${error.retryAfterSeconds} seconds before continuing.`;
  if (error.status === 413)
    return "The upload exceeds the current Studio capacity setting. Your progress is kept; ask an administrator to review the limit.";
  if (error.code === "upload_failed")
    return "Studio could not finish this PDF. Its saved upload is retained. Ask an administrator to review the failure; no text has been trusted.";
  if (error.status === 422 || error.code === "upload_invalid_file")
    return "The selected file could not be accepted. Check that it is the intended, readable PDF and that its details are correct.";
  if (error.code === "upload_response_invalid")
    return "Studio returned an unexpected upload status. Your saved link is kept; refresh before trying again.";
  return "The connection or upload service was interrupted. Your progress is kept; continue to check the saved upload before sending anything again.";
}

function aborted(signal?: AbortSignal) {
  if (signal?.aborted) throw new UploadFailure("upload_paused");
}

async function wait(ms: number, signal?: AbortSignal) {
  aborted(signal);
  await new Promise<void>((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(new UploadFailure("upload_paused"));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", abort, { once: true });
  });
}

function checkSession(
  session: UploadSession,
  expectedId?: string,
): UploadSession {
  const validInteger = (value: number) =>
    Number.isSafeInteger(value) && value >= 0;
  if (
    !isUploadId(session.id) ||
    (expectedId && session.id !== expectedId) ||
    !validInteger(session.size_bytes) ||
    session.size_bytes < 5 ||
    !validInteger(session.next_offset) ||
    session.next_offset > session.size_bytes ||
    (session.next_offset !== session.size_bytes &&
      session.next_offset % UPLOAD_CHUNK_BYTES !== 0) ||
    !validInteger(session.version) ||
    !validInteger(session.verified_bytes) ||
    session.verified_bytes > session.size_bytes ||
    session.chunk_size_bytes !== UPLOAD_CHUNK_BYTES ||
    !["uploading", "pending", "finalizing", "completed", "failed"].includes(
      session.status,
    )
  ) {
    throw new UploadFailure("upload_response_invalid");
  }
  return session;
}

async function lookupUploadRequest(
  api: ApiClient,
  requestId: string,
  signal?: AbortSignal,
): Promise<UploadSession> {
  aborted(signal);
  const result = await api
    .GET("/api/v1/admin/source-uploads/by-request/{request_id}", {
      params: { path: { request_id: requestId } },
      cache: "no-store",
      signal,
    })
    .catch(() => {
      aborted(signal);
      throw new UploadFailure("upload_interrupted");
    });
  aborted(signal);
  if (!result.response.ok || result.error || !result.data)
    throw failure(result.error, result.response);
  const session = checkSession(result.data);
  if (session.request_id !== requestId)
    throw new UploadFailure("upload_response_invalid");
  return session;
}

export async function recoverUploadCheckpoints(
  api: ApiClient,
  signal?: AbortSignal,
): Promise<UploadCheckpoints> {
  const saved = readUploadCheckpoints();
  for (const requestId of saved.requestIds ?? []) {
    try {
      const session = await lookupUploadRequest(api, requestId, signal);
      resolveUploadRequest(requestId, session.id);
    } catch (error) {
      if (
        !(error instanceof UploadFailure) ||
        error.status !== 404 ||
        error.code !== "source_upload_not_found"
      )
        throw error;
    }
  }
  return readUploadCheckpoints();
}

/** One task retains the same immutable File for in-session recovery. Reselection creates a new task. */
export class ResumableSourceUpload {
  private readonly api: ApiClient;
  private readonly file?: File;
  private readonly metadata?: UploadMetadata;
  private id?: string;
  private session?: UploadSession;
  private requestIdentity?: string;
  private running = false;

  constructor(input: {
    api: ApiClient;
    file?: File;
    metadata?: UploadMetadata;
    uploadId?: string;
    requestId?: string;
  }) {
    this.api = input.api;
    this.file = input.file;
    this.metadata = input.metadata
      ? structuredClone(input.metadata)
      : undefined;
    this.id = input.uploadId;
    if (input.requestId !== undefined && !isUploadId(input.requestId))
      throw new UploadFailure("upload_checkpoint_invalid", 0, undefined, false);
    this.requestIdentity = input.requestId?.toLowerCase();
    if (this.id && !isUploadId(this.id))
      throw new UploadFailure("upload_response_invalid");
  }

  get uploadId() {
    return this.id;
  }

  get requestId() {
    return this.requestIdentity;
  }

  private async request<T>(
    send: () => Promise<{ response: Response; data?: T; error?: unknown }>,
    signal?: AbortSignal,
  ): Promise<T> {
    aborted(signal);
    try {
      const result = await send();
      aborted(signal);
      if (!result.response.ok || result.error || !result.data)
        throw failure(result.error, result.response);
      return result.data;
    } catch (error) {
      aborted(signal);
      if (error instanceof UploadFailure) throw error;
      throw new UploadFailure("upload_interrupted");
    }
  }

  private accept(session: UploadSession, options: RunOptions) {
    checkSession(session, this.id);
    if (
      this.session &&
      (session.version < this.session.version ||
        session.next_offset < this.session.next_offset ||
        session.size_bytes !== this.session.size_bytes)
    )
      throw new UploadFailure("upload_changed");
    if (
      this.requestIdentity &&
      session.request_id != null &&
      session.request_id !== this.requestIdentity
    )
      throw new UploadFailure("upload_response_invalid");
    this.session = session;
    this.id = session.id;
    if (this.requestIdentity)
      resolveUploadRequest(this.requestIdentity, session.id);
    options.onSession?.(session);
    return session;
  }

  private async get(options: RunOptions) {
    return this.accept(
      await this.request(
        () =>
          this.api.GET("/api/v1/admin/source-uploads/{upload_id}", {
            params: { path: { upload_id: this.id! } },
            cache: "no-store",
            signal: options.signal,
          }),
        options.signal,
      ),
      options,
    );
  }

  private async receipts(
    offset: number,
    options: RunOptions,
  ): Promise<ReceiptPage> {
    return this.request(
      () =>
        this.api.GET("/api/v1/admin/source-uploads/{upload_id}/chunks", {
          params: {
            path: { upload_id: this.id! },
            query: { offset, limit: 64 },
          },
          cache: "no-store",
          signal: options.signal,
        }),
      options.signal,
    );
  }

  private async chunk(offset: number, size: number, options: RunOptions) {
    aborted(options.signal);
    if (!this.file) throw new UploadFailure("upload_file_required");
    if (!globalThis.crypto?.subtle)
      throw new UploadFailure("upload_crypto_unavailable");
    const bytes = await this.file.slice(offset, offset + size).arrayBuffer();
    if (bytes.byteLength !== size || size > UPLOAD_CHUNK_BYTES)
      throw new UploadFailure("upload_file_mismatch");
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    aborted(options.signal);
    const checksum = [...new Uint8Array(digest)]
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
    return { bytes, checksum };
  }

  private progress(
    options: RunOptions,
    phase: UploadProgress["phase"],
    checkedBytes?: number,
  ) {
    options.onProgress?.({
      phase,
      uploadedBytes: this.session?.next_offset ?? 0,
      totalBytes: this.session?.size_bytes ?? this.file?.size ?? 0,
      checkedBytes,
    });
  }

  private async verifyPrefix(options: RunOptions) {
    const session = this.session!;
    if (!this.file || this.file.size !== session.size_bytes)
      throw new UploadFailure("upload_file_mismatch");
    let offset = 0;
    while (offset < session.next_offset) {
      const page = await this.receipts(offset, options);
      if (
        page.upload_id !== this.id ||
        page.next_offset !== session.next_offset ||
        !page.receipts.length ||
        page.receipts.length > 64
      )
        throw new UploadFailure("upload_receipts_invalid");
      for (const receipt of page.receipts) {
        const size = Math.min(UPLOAD_CHUNK_BYTES, session.size_bytes - offset);
        if (
          receipt.offset !== offset ||
          receipt.size_bytes !== size ||
          offset + size > session.next_offset ||
          !/^[0-9a-f]{64}$/.test(receipt.checksum_sha256)
        )
          throw new UploadFailure("upload_receipts_invalid");
        const actual = await this.chunk(offset, size, options);
        if (actual.checksum !== receipt.checksum_sha256)
          throw new UploadFailure("upload_file_mismatch");
        offset += size;
        this.progress(options, "checking", offset);
      }
      if (
        offset < session.next_offset
          ? page.next_receipt_offset !== offset
          : page.next_receipt_offset != null
      )
        throw new UploadFailure("upload_receipts_invalid");
    }
  }

  private async create(options: RunOptions) {
    if (!this.requestIdentity) {
      if (!this.file || !this.metadata)
        throw new UploadFailure("upload_file_required");
      if (readUploadCheckpoints().creationUncertain)
        throw new UploadFailure("upload_creation_unknown", 0, undefined, false);
      if (!globalThis.crypto?.randomUUID)
        throw new UploadFailure("upload_crypto_unavailable");
      this.requestIdentity = crypto.randomUUID();
    }
    recordUploadRequest(this.requestIdentity);
    options.onCreating?.(true);
    this.progress(options, "creating");
    if (!this.file || !this.metadata) {
      this.accept(
        await lookupUploadRequest(
          this.api,
          this.requestIdentity,
          options.signal,
        ),
        options,
      );
      options.onCreating?.(false);
      return;
    }
    try {
      const session = await this.request(
        () =>
          this.api.POST("/api/v1/admin/source-uploads", {
            body: {
              ...this.metadata!,
              filename: this.file!.name,
              size_bytes: this.file!.size,
              request_id: this.requestIdentity!,
            },
            signal: options.signal,
          }),
        options.signal,
      );
      this.accept(session, options);
      options.onCreating?.(false);
    } catch (error) {
      if (this.id) throw error;
      if (
        error instanceof UploadFailure &&
        ((error.status >= 400 && error.status < 500) ||
          [
            "source_upload_storage_unsupported",
            "source_upload_unavailable",
            "source_upload_staging_unavailable",
          ].includes(error.code))
      )
        throw error;
      throw new UploadFailure(
        "upload_creation_unknown",
        error instanceof UploadFailure ? error.status : 0,
      );
    }
  }

  async run(options: RunOptions = {}): Promise<UploadSession> {
    if (this.running) throw new UploadFailure("upload_in_progress");
    this.running = true;
    try {
      aborted(options.signal);
      if (this.session?.status === "completed") return this.completed();
      if (
        this.file &&
        (!Number.isSafeInteger(this.file.size) ||
          this.file.size < 5 ||
          this.file.type !== "application/pdf" ||
          !this.file.name.toLowerCase().endsWith(".pdf"))
      )
        throw new UploadFailure("upload_invalid_file");
      if (this.file && !globalThis.crypto?.subtle)
        throw new UploadFailure("upload_crypto_unavailable");
      const resuming = Boolean(this.id);
      if (resuming) await this.get(options);
      else await this.create(options);
      if (this.session!.status === "failed")
        throw new UploadFailure("upload_failed");
      if (this.file && this.file.size !== this.session!.size_bytes)
        throw new UploadFailure("upload_file_mismatch");
      if (this.file && (resuming || this.session!.next_offset > 0))
        await this.verifyPrefix(options);
      let recoveries = 0;
      while (
        this.session!.status === "uploading" &&
        this.session!.next_offset < this.session!.size_bytes
      ) {
        if (!this.file) throw new UploadFailure("upload_file_required");
        const offset = this.session!.next_offset;
        const size = Math.min(
          UPLOAD_CHUNK_BYTES,
          this.session!.size_bytes - offset,
        );
        this.progress(options, "uploading");
        const { bytes, checksum } = await this.chunk(offset, size, options);
        try {
          const result = await this.request(
            () =>
              this.api.PUT("/api/v1/admin/source-uploads/{upload_id}/chunks", {
                params: {
                  path: { upload_id: this.id! },
                  query: { offset },
                  header: { "X-Chunk-SHA256": checksum },
                },
                headers: { "Content-Type": "application/octet-stream" },
                body: "",
                bodySerializer: () => bytes,
                signal: options.signal,
              }),
            options.signal,
          );
          if (result.next_offset !== offset + size)
            throw new UploadFailure("upload_changed");
          this.accept(result, options);
        } catch (error) {
          aborted(options.signal);
          const recoverable =
            error instanceof UploadFailure &&
            (error.code === "upload_interrupted" ||
              error.code === "source_upload_offset_conflict" ||
              error.status >= 500);
          if (!recoverable || recoveries >= 3) throw error;
          recoveries += 1;
          await this.get(options);
          if (this.session!.next_offset !== offset + size)
            throw new UploadFailure("upload_interrupted");
          const page = await this.receipts(offset, options);
          const receipt = page.receipts[0];
          if (
            page.upload_id !== this.id ||
            page.next_offset !== offset + size ||
            page.receipts.length !== 1 ||
            !receipt ||
            receipt.offset !== offset ||
            receipt.size_bytes !== size ||
            receipt.checksum_sha256 !== checksum
          )
            throw new UploadFailure("upload_receipts_invalid");
        }
        this.progress(options, "uploading");
      }
      aborted(options.signal);
      if (this.session!.status === "uploading") {
        try {
          this.accept(
            await this.request(
              () =>
                this.api.POST(
                  "/api/v1/admin/source-uploads/{upload_id}/complete",
                  {
                    params: { path: { upload_id: this.id! } },
                    body: { expected_version: this.session!.version },
                    signal: options.signal,
                  },
                ),
              options.signal,
            ),
            options,
          );
        } catch (error) {
          aborted(options.signal);
          if (
            !(error instanceof UploadFailure) ||
            (error.status !== 0 && error.status < 500)
          )
            throw error;
          await this.get(options);
          if (this.session!.status === "uploading")
            throw new UploadFailure("upload_interrupted");
        }
      }
      const maxPolls = Math.max(1, Math.min(options.maxPolls ?? 120, 600));
      const interval = Math.max(
        0,
        Math.min(options.pollIntervalMs ?? 1000, 30_000),
      );
      for (
        let count = 0;
        ["pending", "finalizing"].includes(this.session!.status);
        count += 1
      ) {
        this.progress(options, "finishing");
        if (count >= maxPolls)
          throw new UploadFailure("upload_still_finishing");
        await wait(interval, options.signal);
        await this.get(options);
      }
      return this.completed();
    } finally {
      this.running = false;
    }
  }

  private completed(): UploadSession {
    const session = this.session!;
    if (session.status === "failed") throw new UploadFailure("upload_failed");
    if (
      session.status !== "completed" ||
      !isUploadId(session.document_id) ||
      session.next_offset !== session.size_bytes ||
      session.verified_bytes !== session.size_bytes ||
      !session.checksum_sha256 ||
      !/^[0-9a-f]{64}$/.test(session.checksum_sha256) ||
      (session.source_read_job_id != null &&
        !isUploadId(session.source_read_job_id))
    )
      throw new UploadFailure("upload_response_invalid");
    return session;
  }
}
