import { isUploadId, UploadFailure } from "./source-upload";

export const UPLOAD_CHECKPOINT_KEY = "exam-guru.source-upload-checkpoints.v1";
export type UploadCheckpoints = {
  uploadIds: string[];
  creationUncertain: boolean;
  requestIds?: string[];
};

export function readUploadCheckpoints(): UploadCheckpoints {
  let raw: string | null;
  try {
    raw = localStorage.getItem(UPLOAD_CHECKPOINT_KEY);
  } catch {
    throw new UploadFailure(
      "upload_checkpoint_unavailable",
      0,
      undefined,
      false,
    );
  }
  if (raw === null) return { uploadIds: [], creationUncertain: false };
  try {
    if (raw.length > 4096) throw new Error("invalid checkpoint");
    const value: unknown = JSON.parse(raw);
    if (
      !value ||
      typeof value !== "object" ||
      !("uploadIds" in value) ||
      !Array.isArray(value.uploadIds) ||
      value.uploadIds.length > 64 ||
      !value.uploadIds.every(isUploadId) ||
      !("creationUncertain" in value) ||
      typeof value.creationUncertain !== "boolean"
    )
      throw new Error("invalid checkpoint");
    const requestIds = "requestIds" in value ? value.requestIds : [];
    if (
      !Array.isArray(requestIds) ||
      requestIds.length + value.uploadIds.length > 64 ||
      !requestIds.every(isUploadId) ||
      Object.keys(value).some(
        (key) =>
          !["uploadIds", "requestIds", "creationUncertain"].includes(key),
      )
    )
      throw new Error("invalid checkpoint");
    return {
      uploadIds: [...new Set(value.uploadIds)],
      creationUncertain: value.creationUncertain || requestIds.length > 0,
      ...(requestIds.length ? { requestIds: [...new Set(requestIds)] } : {}),
    };
  } catch {
    throw new UploadFailure("upload_checkpoint_invalid", 0, undefined, false);
  }
}

function write(value: UploadCheckpoints): UploadCheckpoints {
  const requestIds = [...new Set(value.requestIds ?? [])];
  value = {
    uploadIds: [...new Set(value.uploadIds)],
    creationUncertain: value.creationUncertain || requestIds.length > 0,
    ...(requestIds.length ? { requestIds } : {}),
  };
  if (value.uploadIds.length + requestIds.length > 64)
    throw new UploadFailure(
      "upload_checkpoint_unavailable",
      0,
      undefined,
      false,
    );
  try {
    // Only server-owned session identifiers and the create-recovery guard are persisted.
    localStorage.setItem(UPLOAD_CHECKPOINT_KEY, JSON.stringify(value));
  } catch {
    throw new UploadFailure(
      "upload_checkpoint_unavailable",
      0,
      undefined,
      false,
    );
  }
  return value;
}

export function markUploadCreation(
  creationUncertain: boolean,
): UploadCheckpoints {
  return write({ ...readUploadCheckpoints(), creationUncertain });
}

export function recordUploadCheckpoint(
  id: string,
  creationResolved = false,
): UploadCheckpoints {
  if (!isUploadId(id))
    throw new UploadFailure("upload_checkpoint_invalid", 0, undefined, false);
  const previous = readUploadCheckpoints();
  return write({
    ...previous,
    uploadIds: [...new Set([...previous.uploadIds, id])],
    creationUncertain: creationResolved ? false : previous.creationUncertain,
  });
}

export function recordUploadRequest(requestId: string): UploadCheckpoints {
  if (!isUploadId(requestId))
    throw new UploadFailure("upload_checkpoint_invalid", 0, undefined, false);
  const previous = readUploadCheckpoints();
  if (previous.creationUncertain && !previous.requestIds?.length)
    throw new UploadFailure("upload_creation_unknown", 0, undefined, false);
  return write({
    ...previous,
    requestIds: [...new Set([...(previous.requestIds ?? []), requestId])],
    creationUncertain: true,
  });
}

export function resolveUploadRequest(
  requestId: string,
  uploadId: string,
): UploadCheckpoints {
  if (!isUploadId(requestId) || !isUploadId(uploadId))
    throw new UploadFailure("upload_checkpoint_invalid", 0, undefined, false);
  const previous = readUploadCheckpoints();
  const requestIds = (previous.requestIds ?? []).filter(
    (id) => id !== requestId,
  );
  return write({
    ...previous,
    uploadIds: [...new Set([...previous.uploadIds, uploadId])],
    requestIds,
    creationUncertain: previous.requestIds?.includes(requestId)
      ? requestIds.length > 0
      : previous.creationUncertain,
  });
}

export function finishUploadCheckpoint(id: string): UploadCheckpoints {
  const previous = readUploadCheckpoints();
  // Removing a completed browser link does not delete or free any retained server staging.
  return write({
    ...previous,
    uploadIds: previous.uploadIds.filter((candidate) => candidate !== id),
  });
}
