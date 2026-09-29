import type { Health, Mode, Report, SessionData } from "./types";

async function parse<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = `Request failed (${r.status})`;
    try {
      const body = await r.json();
      if (typeof body.detail === "string") msg = body.detail;
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

export const api = {
  health: () => fetch("/api/health").then((r) => parse<Health>(r)),
  samples: () => fetch("/api/samples").then((r) => parse<{ name: string; kind: string }[]>(r)),
  upload(file: File, mode: Mode): Promise<SessionData> {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("mode", mode);
    return fetch("/api/upload", { method: "POST", body: fd }).then((r) => parse<SessionData>(r));
  },
  startSample(name: string, mode: Mode): Promise<SessionData> {
    const fd = new FormData();
    fd.append("mode", mode);
    return fetch(`/api/samples/${encodeURIComponent(name)}`, { method: "POST", body: fd }).then((r) => parse<SessionData>(r));
  },
  session: (id: string) => fetch(`/api/sessions/${encodeURIComponent(id)}`).then((r) => parse<SessionData>(r)),
  setMode(id: string, mode: Mode) {
    const fd = new FormData();
    fd.append("mode", mode);
    return fetch(`/api/sessions/${encodeURIComponent(id)}/mode`, { method: "POST", body: fd }).then((r) => parse<{ mode: Mode }>(r));
  },
  restart: (id: string) => fetch(`/api/sessions/${encodeURIComponent(id)}/restart`, { method: "POST" }).then((r) => parse(r)),
  remove: (id: string) => fetch(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }).then((r) => parse(r)),
  report: (id: string) =>
    fetch(`/api/sessions/${encodeURIComponent(id)}/report`).then((r) =>
      parse<{ status: "ready" | "generating" | "not_ended"; report?: Report }>(r),
    ),
  async transcribe(blob: Blob): Promise<string> {
    const r = await fetch("/api/transcribe", { method: "POST", body: blob, headers: { "Content-Type": blob.type || "audio/webm" } });
    const body = await parse<{ text: string }>(r);
    return body.text;
  },
  slideUrl: (id: string, n: number) => `/api/sessions/${encodeURIComponent(id)}/slides/${n}.png`,
};

export function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}
