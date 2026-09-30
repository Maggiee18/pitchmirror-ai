import type { Enrichment, FeedbackItem, LiveMetrics, Question, Segment, ServerEvent, SessionData, SlideAnalysis } from "./types";

export interface Notice {
  id: number;
  level: "info" | "warn" | "error";
  message: string;
}

export interface LiveState {
  session: SessionData | null;
  analyzing: number[];
  questionPending: boolean;
  evaluating: string[];
  notices: Notice[];
  fatal: string | null;
  reportReady: boolean;
}

export const initialLive: LiveState = {
  session: null, analyzing: [], questionPending: false, evaluating: [], notices: [], fatal: null, reportReady: false,
};

let noticeId = 0;

export type Action =
  | { type: "server"; event: ServerEvent }
  | { type: "local_slide"; n: number }
  | { type: "dismiss"; id: number }
  | { type: "notice"; level: Notice["level"]; message: string }
  | { type: "set_session"; session: SessionData };

function upsert<T extends { id: string }>(list: T[], item: T): T[] {
  const i = list.findIndex((x) => x.id === item.id);
  if (i === -1) return [...list, item];
  const copy = list.slice();
  copy[i] = item;
  return copy;
}

function withNotice(state: LiveState, level: Notice["level"], message: string): LiveState {
  if (state.notices.some((n) => n.message === message)) return state;
  return { ...state, notices: [...state.notices.slice(-3), { id: ++noticeId, level, message }] };
}

export function reducer(state: LiveState, action: Action): LiveState {
  if (action.type === "dismiss") return { ...state, notices: state.notices.filter((n) => n.id !== action.id) };
  if (action.type === "notice") return withNotice(state, action.level, action.message);
  if (action.type === "set_session") return { ...state, session: action.session };
  const s = state.session;
  if (action.type === "local_slide") return s ? { ...state, session: { ...s, current_slide: action.n } } : state;

  const e = action.event;
  switch (e.type) {
    case "snapshot":
      return { ...state, session: e.session, fatal: null, reportReady: e.session.has_report };
    case "fatal":
      return { ...state, fatal: e.message };
    case "notice":
      return withNotice(state, e.level, e.message);
    case "question_pending":
      return { ...state, questionPending: e.active };
    case "analyzing":
      return {
        ...state,
        analyzing: e.active ? [...new Set([...state.analyzing, e.slide_number])] : state.analyzing.filter((n) => n !== e.slide_number),
      };
    case "report_ready":
      return { ...state, reportReady: true };
  }
  if (!s) return state;
  switch (e.type) {
    case "segment":
      if (s.segments.some((x: Segment) => x.seq === e.item.seq)) return state;
      return { ...state, session: { ...s, segments: [...s.segments, e.item], last_seq: Math.max(s.last_seq, e.item.seq) } };
    case "feedback":
      return { ...state, session: { ...s, feedback: upsert<FeedbackItem>(s.feedback, e.item) } };
    case "question":
      return { ...state, session: { ...s, questions: upsert<Question>(s.questions, e.item) } };
    case "question_update":
      return {
        ...state,
        evaluating: e.evaluating ? [...state.evaluating, e.item.id] : state.evaluating.filter((id) => id !== e.item.id),
        session: { ...s, questions: upsert<Question>(s.questions, e.item) },
      };
    case "metrics":
      return { ...state, session: { ...s, metrics: e.metrics as LiveMetrics } };
    case "slide_updated":
      return {
        ...state,
        session: {
          ...s,
          slides: s.slides.map((x) => (x.slide_number === e.slide.slide_number ? e.slide : x)),
          enrichment: e.enrichment as Enrichment,
        },
      };
    case "enrichment":
      return { ...state, session: { ...s, enrichment: e.enrichment } };
    case "slide_analysis":
      return {
        ...(e.error ? withNotice(state, "warn", `AI analysis unavailable (${e.error}); using the rule engine.`) : state),
        session: { ...s, analysis: { ...s.analysis, [String(e.state.slide_number)]: e.state as SlideAnalysis } },
      };
    case "status":
      return {
        ...state,
        session: { ...s, status: e.status, started_at: e.started_at ?? s.started_at },
      };
    case "slide":
      return { ...state, session: { ...s, current_slide: e.slide_number } };
    default:
      return state;
  }
}
