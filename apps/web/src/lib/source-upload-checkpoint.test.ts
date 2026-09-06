import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  readUploadCheckpoints,
  recordUploadCheckpoint,
  markUploadCreation,
  finishUploadCheckpoint,
  UPLOAD_CHECKPOINT_KEY,
  recordUploadRequest,
  resolveUploadRequest,
} from "./source-upload-checkpoint";

const id = "00000000-0000-0000-0000-000000000971";
const other = "00000000-0000-0000-0000-000000000972";
beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe("upload recovery links", () => {
  it("persists a request UUID before a session exists and atomically replaces only its recovery link", () => {
    const first = "00000000-0000-0000-0000-000000000973";
    const second = "00000000-0000-0000-0000-000000000974";
    recordUploadRequest(first);
    recordUploadRequest(second);
    expect(readUploadCheckpoints()).toEqual({
      uploadIds: [],
      requestIds: [first, second],
      creationUncertain: true,
    });
    recordUploadCheckpoint(other, true);
    expect(readUploadCheckpoints().requestIds).toEqual([first, second]);
    resolveUploadRequest(first, id);
    expect(readUploadCheckpoints()).toEqual({
      uploadIds: [other, id],
      requestIds: [second],
      creationUncertain: true,
    });
    resolveUploadRequest(second, other);
    expect(readUploadCheckpoints()).toEqual({
      uploadIds: [other, id],
      creationUncertain: false,
    });
    expect(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)).not.toMatch(
      /filename|metadata|checksum|token|size_bytes/,
    );
  });

  it("retains unresolved request identities when a different upload or legacy callback resolves", () => {
    recordUploadRequest(other);
    recordUploadCheckpoint(id, true);
    markUploadCreation(false);
    finishUploadCheckpoint(id);
    expect(readUploadCheckpoints()).toEqual({
      uploadIds: [],
      requestIds: [other],
      creationUncertain: true,
    });
  });

  it("rejects invalid or over-capacity request checkpoints without losing existing recovery links", () => {
    expect(() => recordUploadRequest("../not-a-uuid")).toThrow(
      "upload_checkpoint_invalid",
    );
    for (let index = 1; index <= 64; index += 1) {
      recordUploadRequest(
        `00000000-0000-0000-0000-${String(index).padStart(12, "0")}`,
      );
    }
    const saved = localStorage.getItem(UPLOAD_CHECKPOINT_KEY);
    expect(() => recordUploadRequest(id)).toThrow(
      "upload_checkpoint_unavailable",
    );
    expect(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)).toBe(saved);
  });

  it("persists only opaque session IDs and uncertain-creation state, never a File or private metadata", () => {
    markUploadCreation(true);
    expect(readUploadCheckpoints()).toEqual({
      uploadIds: [],
      creationUncertain: true,
    });
    recordUploadCheckpoint(id, true);
    recordUploadCheckpoint(other);
    recordUploadCheckpoint(id);
    expect(JSON.parse(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)!)).toEqual({
      uploadIds: [id, other],
      creationUncertain: false,
    });
    finishUploadCheckpoint(id);
    expect(readUploadCheckpoints()).toEqual({
      uploadIds: [other],
      creationUncertain: false,
    });
  });

  it("does not clear a lost new-create response when merely refreshing a different saved upload", () => {
    recordUploadCheckpoint(id);
    markUploadCreation(true);
    recordUploadCheckpoint(id);
    finishUploadCheckpoint(id);
    expect(readUploadCheckpoints()).toEqual({
      uploadIds: [],
      creationUncertain: true,
    });
  });

  it.each([
    ["null", null],
    ["a primitive", "not a checkpoint"],
    ["an array", []],
    ["missing upload IDs", { creationUncertain: false }],
    ["non-array upload IDs", { uploadIds: id, creationUncertain: false }],
    [
      "too many upload IDs",
      { uploadIds: Array(65).fill(id), creationUncertain: false },
    ],
    [
      "invalid upload IDs",
      { uploadIds: ["../source"], creationUncertain: false },
    ],
    ["missing creation guard", { uploadIds: [] }],
    [
      "non-boolean creation guard",
      { uploadIds: [], creationUncertain: "false" },
    ],
    [
      "non-array request IDs",
      { uploadIds: [], requestIds: null, creationUncertain: false },
    ],
    [
      "combined over-capacity IDs",
      {
        uploadIds: Array(32).fill(id),
        requestIds: Array(33).fill(other),
        creationUncertain: true,
      },
    ],
    [
      "invalid request IDs",
      {
        uploadIds: [],
        requestIds: ["request-with-a-filename.pdf"],
        creationUncertain: true,
      },
    ],
    [
      "private metadata",
      { uploadIds: [], creationUncertain: false, filename: "private.pdf" },
    ],
    [
      "prototype properties",
      JSON.parse(
        '{"uploadIds":[],"creationUncertain":false,"__proto__":{"admin":true}}',
      ),
    ],
  ])(
    "rejects %s without discarding the recovery record or allowing a new create",
    (_name, value) => {
      const saved = JSON.stringify(value);
      localStorage.setItem(UPLOAD_CHECKPOINT_KEY, saved);
      const failure = expect.objectContaining({
        code: "upload_checkpoint_invalid",
        status: 0,
        safeToRetry: false,
      });
      expect(() => readUploadCheckpoints()).toThrow(failure);
      expect(() => recordUploadRequest(other)).toThrow(failure);
      expect(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)).toBe(saved);
    },
  );

  it("accepts the exact serialized-size boundary and rejects larger valid JSON without overwriting it", () => {
    const valid = JSON.stringify({ uploadIds: [id], creationUncertain: false });
    localStorage.setItem(UPLOAD_CHECKPOINT_KEY, valid.padEnd(4096));
    expect(readUploadCheckpoints()).toEqual({
      uploadIds: [id],
      creationUncertain: false,
    });
    const oversized = valid.padEnd(4097);
    localStorage.setItem(UPLOAD_CHECKPOINT_KEY, oversized);
    expect(() => readUploadCheckpoints()).toThrow("upload_checkpoint_invalid");
    expect(() => markUploadCreation(false)).toThrow(
      "upload_checkpoint_invalid",
    );
    expect(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)).toBe(oversized);
  });

  it("deduplicates opaque IDs and derives uncertainty from pending requests even if an old guard says false", () => {
    localStorage.setItem(
      UPLOAD_CHECKPOINT_KEY,
      JSON.stringify({
        uploadIds: [id, id],
        requestIds: [other, other],
        creationUncertain: false,
      }),
    );
    expect(readUploadCheckpoints()).toEqual({
      uploadIds: [id],
      requestIds: [other],
      creationUncertain: true,
    });
    recordUploadRequest(other);
    expect(JSON.parse(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)!)).toEqual({
      uploadIds: [id],
      requestIds: [other],
      creationUncertain: true,
    });
  });

  it("fails closed when browser storage cannot be read, without writing a replacement checkpoint", () => {
    const write = vi.spyOn(Storage.prototype, "setItem");
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("Storage disabled", "SecurityError");
    });
    const failure = expect.objectContaining({
      code: "upload_checkpoint_unavailable",
      status: 0,
      safeToRetry: false,
    });
    expect(() => readUploadCheckpoints()).toThrow(failure);
    expect(() => recordUploadCheckpoint(id)).toThrow(failure);
    expect(write).not.toHaveBeenCalled();
  });

  it("keeps the write-ahead request when replacing it with an acknowledged session exceeds browser quota", () => {
    recordUploadRequest(id);
    const saved = localStorage.getItem(UPLOAD_CHECKPOINT_KEY);
    const write = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw new DOMException("Quota reached", "QuotaExceededError");
      });
    expect(() => resolveUploadRequest(id, other)).toThrow(
      expect.objectContaining({
        code: "upload_checkpoint_unavailable",
        safeToRetry: false,
      }),
    );
    expect(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)).toBe(saved);
    write.mockRestore();
    expect(resolveUploadRequest(id, other)).toEqual({
      uploadIds: [other],
      creationUncertain: false,
    });
  });

  it.each([
    ["record an invalid session", () => recordUploadCheckpoint("not-a-uuid")],
    [
      "resolve an invalid request",
      () => resolveUploadRequest("../request", other),
    ],
    ["resolve an invalid session", () => resolveUploadRequest(id, "../upload")],
  ])("does not %s or alter existing checkpoints", (_name, operation) => {
    recordUploadRequest(id);
    const saved = localStorage.getItem(UPLOAD_CHECKPOINT_KEY);
    expect(operation).toThrow(
      expect.objectContaining({
        code: "upload_checkpoint_invalid",
        safeToRetry: false,
      }),
    );
    expect(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)).toBe(saved);
  });

  it("cannot replace an unkeyed legacy create guard with an invented request UUID", () => {
    markUploadCreation(true);
    const saved = localStorage.getItem(UPLOAD_CHECKPOINT_KEY);
    expect(() => recordUploadRequest(id)).toThrow(
      expect.objectContaining({
        code: "upload_creation_unknown",
        safeToRetry: false,
      }),
    );
    expect(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)).toBe(saved);
  });

  it.each([false, true])(
    "does not resolve unrelated creation state when acknowledging an unrecorded request (legacy guard: %s)",
    (creationUncertain) => {
      markUploadCreation(creationUncertain);
      expect(resolveUploadRequest(id, other)).toEqual({
        uploadIds: [other],
        creationUncertain,
      });
    },
  );

  it("preserves a different pending request when an unrecorded request is acknowledged", () => {
    recordUploadRequest(id);
    const unrelated = "00000000-0000-0000-0000-000000000975";
    expect(resolveUploadRequest(unrelated, other)).toEqual({
      uploadIds: [other],
      requestIds: [id],
      creationUncertain: true,
    });
  });

  it("applies capacity to combined unique links and permits atomic request-to-session replacement at capacity", () => {
    const ids = Array.from(
      { length: 64 },
      (_, index) =>
        `00000000-0000-0000-0000-${String(index + 1).padStart(12, "0")}`,
    );
    localStorage.setItem(
      UPLOAD_CHECKPOINT_KEY,
      JSON.stringify({
        uploadIds: ids.slice(0, 32),
        requestIds: ids.slice(32),
        creationUncertain: true,
      }),
    );
    recordUploadCheckpoint(ids[0]);
    recordUploadRequest(ids[32]);
    const saved = localStorage.getItem(UPLOAD_CHECKPOINT_KEY);
    expect(() => recordUploadCheckpoint(id)).toThrow(
      "upload_checkpoint_unavailable",
    );
    expect(localStorage.getItem(UPLOAD_CHECKPOINT_KEY)).toBe(saved);
    const resolved = resolveUploadRequest(ids[32], id);
    expect(resolved.uploadIds).toHaveLength(33);
    expect(resolved.requestIds).toHaveLength(31);
    expect(resolved.requestIds).not.toContain(ids[32]);
    expect(resolved.creationUncertain).toBe(true);
  });

  it("fails closed on corrupt recovery state instead of silently authorizing a duplicate create", () => {
    localStorage.setItem(UPLOAD_CHECKPOINT_KEY, "{broken");
    expect(() => readUploadCheckpoints()).toThrow("upload_checkpoint_invalid");
  });
});
