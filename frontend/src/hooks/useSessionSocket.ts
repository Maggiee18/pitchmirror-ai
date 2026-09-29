import { useCallback, useEffect, useRef, useState } from "react";
import type { ServerEvent } from "../types";

export type ConnState = "connecting" | "open" | "reconnecting" | "closed";

type Outgoing = Record<string, unknown> & { type: string };

/**
 * WebSocket with automatic reconnection. Transcript messages carry a sequence number and stay in an
 * outbox until the server acknowledges them, so nothing said during a network blip is lost.
 */
export function useSessionSocket(sessionId: string | null, onEvent: (e: ServerEvent) => void) {
  const [conn, setConn] = useState<ConnState>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;
  const outbox = useRef<Map<number, Outgoing>>(new Map());
  const controlQueue = useRef<Outgoing[]>([]);
  const seqRef = useRef(0);
  const attempts = useRef(0);
  const stopped = useRef(false);

  useEffect(() => {
    if (!sessionId) return;
    stopped.current = false;
    let pingTimer: number | undefined;
    let retryTimer: number | undefined;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${location.host}/ws/${encodeURIComponent(sessionId)}`);
      wsRef.current = ws;
      ws.onopen = () => {
        attempts.current = 0;
        setConn("open");
        const queued = controlQueue.current.splice(0);
        queued.forEach((m) => ws.send(JSON.stringify(m)));
        [...outbox.current.entries()].sort((a, b) => a[0] - b[0]).forEach(([, m]) => ws.send(JSON.stringify(m)));
        pingTimer = window.setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "ping", t: Date.now() }));
        }, 15000);
      };
      ws.onmessage = (ev) => {
        let msg: ServerEvent;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (msg.type === "ack") outbox.current.delete(msg.seq);
        if (msg.type === "fatal") stopped.current = true;
        handlerRef.current(msg);
      };
      ws.onclose = () => {
        window.clearInterval(pingTimer);
        if (stopped.current) {
          setConn("closed");
          return;
        }
        setConn("reconnecting");
        const delay = Math.min(8000, 400 * 2 ** attempts.current);
        attempts.current += 1;
        retryTimer = window.setTimeout(connect, delay);
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => {
      stopped.current = true;
      window.clearInterval(pingTimer);
      window.clearTimeout(retryTimer);
      wsRef.current?.close();
    };
  }, [sessionId]);

  const send = useCallback((msg: Outgoing) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
    else controlQueue.current.push(msg);
  }, []);

  const sendTranscript = useCallback((msg: Omit<Outgoing, "type" | "seq">) => {
    seqRef.current += 1;
    const full: Outgoing = { ...msg, type: "transcript", seq: seqRef.current };
    outbox.current.set(seqRef.current, full);
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(full));
  }, []);

  const syncSeq = useCallback((serverLastSeq: number) => {
    seqRef.current = Math.max(seqRef.current, serverLastSeq);
  }, []);

  const resetOutbox = useCallback(() => {
    outbox.current.clear();
    controlQueue.current = [];
    seqRef.current = 0;
  }, []);

  return { conn, send, sendTranscript, syncSeq, resetOutbox, pending: () => outbox.current.size };
}
