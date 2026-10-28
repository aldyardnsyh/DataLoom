/**
 * Presigned CSV/Parquet upload (FR-013, D-02).
 *
 * The API never sees file bytes: `init` returns presigned PUT urls, the
 * browser uploads each part directly to object storage, then `complete`
 * finalizes. With the mock API this flow fails at `init` with a clear 404,
 * which is the honest behaviour (no fake progress).
 *
 * Demo mode: `init` is served by the in-browser demo backend, whose
 * `demo://upload` part URL is not a real endpoint. The presigned PUT is the one
 * request in this app that does NOT go through `apiFetch`, so it must be
 * short-circuited explicitly -- otherwise the browser would try to PUT the
 * user's file to a URL handed out by a fixture.
 */

import { ApiClientError, apiFetch } from "@/lib/api";
import { isDemoMode } from "@/lib/demo/mode";

export interface UploadInitResponse {
  upload_id: string;
  dataset_id: string;
  storage_key: string;
  parts: { part_number: number; url: string; headers: Record<string, string> }[];
  expires_at: string;
  max_bytes: number;
}

export interface UploadCompleteResponse {
  upload_id: string;
  dataset_id: string;
  status: string;
  storage_uri: string;
  size_bytes: number;
  ingest_job_id: string | null;
}

const MAX_BYTES = 524_288_000; // 500 MB, FR-013.

export function contentTypeFor(filename: string): string {
  return filename.toLowerCase().endsWith(".parquet")
    ? "application/vnd.apache.parquet"
    : "text/csv";
}

export function validateFile(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!name.endsWith(".csv") && !name.endsWith(".parquet")) {
    return "Tipe berkas tidak didukung. Gunakan .csv atau .parquet.";
  }
  if (file.size <= 0) return "Berkas kosong.";
  if (file.size > MAX_BYTES) return "Berkas melebihi 500 MB.";
  return null;
}

/** Upload one file end-to-end. Progress is per-part (single PUT for MVP). */
export async function uploadFile(
  projectId: string,
  file: File,
  onProgress?: (fraction: number) => void,
): Promise<UploadCompleteResponse> {
  const init = await apiFetch<UploadInitResponse>(`/projects/${projectId}/uploads`, {
    method: "POST",
    body: {
      filename: file.name,
      content_type: contentTypeFor(file.name),
      size_bytes: file.size,
      part_count: 1,
    },
  });

  const part = init.parts[0];
  if (!part) {
    throw new ApiClientError("Server tidak memberikan URL unggah.", "upload_no_parts", 500, "unknown");
  }

  // Demo: no bytes leave the browser. Report progress as an instant 0 -> 1 so
  // the UI stays honest about what happened (nothing was transmitted).
  if (isDemoMode()) {
    onProgress?.(1);
    return apiFetch<UploadCompleteResponse>(`/projects/${projectId}/uploads/${init.upload_id}/complete`, {
      method: "POST",
      body: { etags: [], checksum_sha256: null },
    });
  }

  const uploaded = await new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", part.url);
    for (const [key, value] of Object.entries(part.headers ?? {})) {
      xhr.setRequestHeader(key, value);
    }
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new ApiClientError(`Unggah ke penyimpanan gagal (${xhr.status}).`, "upload_put_failed", xhr.status, "unknown"));
    };
    xhr.onerror = () => reject(new ApiClientError("Koneksi ke penyimpanan terputus.", "upload_network", 0, "unknown"));
    xhr.send(file);
  });
  void uploaded;

  return apiFetch<UploadCompleteResponse>(`/projects/${projectId}/uploads/${init.upload_id}/complete`, {
    method: "POST",
    body: { etags: [], checksum_sha256: null },
  });
}
