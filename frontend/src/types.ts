export type Mode = "pitch" | "viva" | "interview" | "presentation";
export type SessionStatus = "ready" | "live" | "ended";

export interface VisualElement {
  kind: "chart" | "table" | "diagram" | "image";
  description: string;
  source: "detected" | "pptx" | "vision";
  data_summary: string;
}

export interface SlideContext {
  slide_number: number;
  title: string;
  text: string[];
  notes: string;
  visual_elements: VisualElement[];
  visual_description: string;
  key_concepts: string[];
  important_elements: string[];
  numbers: string[];
  image_file: string;
  word_count: number;
  is_empty: boolean;
  enriched: boolean;
}

export interface FeedbackItem {
  id: string;
  slide_number: number;
  kind: "positive" | "improvement" | "warning";
  category: string;
  severity: "low" | "medium" | "high";
  observation: string;
  evidence_slide: string;
  evidence_speech: string;
  explanation: string;
  suggestion: string;
  source: "rule" | "ai";
  created_at: number;
}

export interface AnswerEvaluation {
  score: number | null;
  addressed_question: boolean | null;
  strengths: string[];
  gaps: string[];
  summary: string;
  source: "rule" | "ai";
}

export interface Question {
  id: string;
  slide_number: number;
  question: string;
  reason: string;
  difficulty: "easy" | "medium" | "hard";
  depth: number;
  parent_id: string | null;
  status: "open" | "answered" | "skipped";
  answer_text: string;
  evaluation: AnswerEvaluation | null;
  source: "rule" | "ai";
  created_at: number;
}

export interface Segment {
  seq: number;
  text: string;
  slide_number: number;
  t_start: number;
  t_end: number;
  kind: "speech" | "answer";
  question_id: string | null;
  source: "browser" | "whisper" | "typed";
}

export interface VisualStatus {
  element: string;
  explained: "yes" | "partial" | "no" | "unknown";
  evidence: string;
}

export interface SlideAnalysis {
  slide_number: number;
  covered_concepts: string[];
  missed_concepts: string[];
  visuals: VisualStatus[];
  reading_ratio: number | null;
  words_analyzed: number;
  source: "rule" | "ai" | "none";
  last_error: string;
}

export interface ProviderInfo {
  name: string;
  model: string | null;
  vision: boolean;
  mode: "ai" | "offline";
  health: { calls: number; failures: number; avg_latency_s: number; last_error: string; circuit_open: boolean };
}

export interface LiveMetrics {
  elapsed_s: number;
  total_words: number;
  wpm: number | null;
  filler_total: number;
  fillers: Record<string, number>;
  pause_count: number;
  long_pause_count: number;
  speaking_time_s: number;
  speaking_time_source: string;
}

export interface Enrichment {
  status: string;
  done: number;
  total: number;
  reason?: string;
  enriched?: number;
}

export interface SessionData {
  id: string;
  filename: string;
  mode: Mode;
  status: SessionStatus;
  slides: SlideContext[];
  enrichment: Enrichment;
  provider: ProviderInfo;
  current_slide: number;
  started_at: number | null;
  elapsed_s: number;
  segments: Segment[];
  feedback: FeedbackItem[];
  questions: Question[];
  metrics: LiveMetrics;
  last_seq: number;
  has_report: boolean;
  analysis: Record<string, SlideAnalysis>;
}

export interface Health {
  ok: boolean;
  llm: ProviderInfo;
  stt: { server_whisper: boolean; provider: string | null };
  limits: { max_upload_mb: number; max_slides: number };
}

export interface Score {
  name: string;
  value: number | null;
  derivation: string[];
}

export interface Report {
  session_id: string;
  generated_at: number;
  provider: ProviderInfo;
  overview: { mode: Mode; filename: string; duration_s: number; slides_total: number; slides_presented: number; slides_not_presented: number[] };
  delivery: {
    raw: {
      elapsed_s: number;
      total_words: number;
      speaking_time_s: number;
      speaking_time_source: string;
      wpm: number | null;
      filler_total: number;
      fillers: Record<string, number>;
      fillers_per_100_words: number | null;
      pause_count: number;
      long_pause_count: number;
      longest_pause_s: number;
      pauses_measured: boolean;
      repetition: { immediate_repeats: { word: string; count: number }[]; repeated_phrases: { phrase: string; count: number }[] };
      rushed_segments: { slide_number: number; wpm: number; excerpt: string }[];
      per_slide: Record<string, { words: number; fillers: number; time_on_slide_s: number; voiced_s: number; wpm: number | null }>;
    };
    caveats: string[];
    interpretation: { kind: "positive" | "improvement"; text: string; suggestion: string }[];
  };
  consistency: {
    per_slide: {
      slide_number: number; title: string; words: number; time_on_slide_s: number; presented: boolean;
      covered: string[]; missed: string[]; reading_ratio: number | null; source: string; note: string;
    }[];
    mismatches: FeedbackItem[];
    other_issues: FeedbackItem[];
    rejected_ungrounded_items: number;
  };
  visuals: { items: { slide_number: number; element: string; explained: string; evidence: string }[]; counts: Record<string, number> };
  questions: { asked: Question[]; weak: Question[] };
  feedback: FeedbackItem[];
  scores: Score[];
  scores_note: string;
  narrative: {
    summary: string;
    top_recommendations: { title: string; detail: string; evidence: string }[];
    practice_questions: { question: string; slide_number: number | null; why: string }[];
    source: "rule" | "ai";
    error?: string;
  };
}

export type ServerEvent =
  | { type: "snapshot"; session: SessionData }
  | { type: "segment"; item: Segment }
  | { type: "ack"; seq: number }
  | { type: "feedback"; item: FeedbackItem }
  | { type: "question"; item: Question }
  | { type: "question_update"; item: Question; evaluating?: boolean }
  | { type: "question_pending"; active: boolean }
  | { type: "metrics"; metrics: LiveMetrics }
  | { type: "slide_updated"; slide: SlideContext; enrichment: Enrichment }
  | { type: "enrichment"; enrichment: Enrichment }
  | { type: "analyzing"; slide_number: number; active: boolean }
  | { type: "slide_analysis"; state: SlideAnalysis; rejected_total: number; error: string | null }
  | { type: "status"; status: SessionStatus; started_at?: number }
  | { type: "slide"; slide_number: number }
  | { type: "report_ready" }
  | { type: "notice"; level: "info" | "warn" | "error"; message: string }
  | { type: "fatal"; message: string }
  | { type: "pong"; t?: number };

export const MODE_INFO: Record<Mode, { label: string; blurb: string; audience: string }> = {
  pitch: { label: "Pitch", blurb: "Problem, differentiation, evidence behind your numbers.", audience: "Investor panel" },
  viva: { label: "Viva", blurb: "Methodology, design decisions, limitations, alternatives.", audience: "Examiner" },
  interview: { label: "Interview", blurb: "Depth behind your technical and project claims.", audience: "Interviewer" },
  presentation: { label: "Presentation", blurb: "Clarity, structure, and explaining your visuals.", audience: "Audience" },
};
