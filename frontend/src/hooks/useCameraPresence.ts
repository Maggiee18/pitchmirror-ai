import { useEffect, useRef, useState } from "react";

/**
 * Webcam presence, computed entirely in the browser with MediaPipe Face Landmarker.
 * Only frame COUNTS leave the browser (faced audience / looked down / turned away); video is never uploaded.
 *
 * The model files load from Google's CDN by default. For offline use, put face_landmarker.task in
 * frontend/public/models/ and the wasm folder in frontend/public/mediapipe/; local copies are tried first.
 */

const WASM_CDN = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1/wasm";
const MODEL_CDN = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task";
const LOCAL_WASM = "/mediapipe/wasm";
const LOCAL_MODEL = "/models/face_landmarker.task";

const FPS = 10;
const CALIBRATION_FRAMES = 15; // ~1.5s looking at the camera
const REPORT_EVERY_MS = 2000;
const LOOK_AWAY_MIN_S = 6;

export type CameraState = "off" | "starting" | "calibrating" | "on" | "denied" | "error";
export type Gaze = "audience" | "down" | "turned" | "no-face";

export interface PresenceSample {
  frames: number;
  face_frames: number;
  engaged_frames: number;
  down_frames: number;
  turned_frames: number;
  seconds: number;
}

interface Options {
  enabled: boolean;
  videoRef: React.RefObject<HTMLVideoElement>;
  onSample: (s: PresenceSample) => void;
  onLookAway: (durationS: number) => void;
}

async function exists(url: string): Promise<boolean> {
  try {
    const r = await fetch(url, { method: "HEAD" });
    return r.ok && !(r.headers.get("content-type") || "").includes("text/html");
  } catch {
    return false;
  }
}

function blend(cats: { categoryName: string; score: number }[] | undefined, name: string): number {
  return cats?.find((c) => c.categoryName === name)?.score ?? 0;
}

/** Yaw/pitch in degrees from MediaPipe's column-major 4x4 facial transformation matrix. */
function headAngles(m: number[]): { yaw: number; pitch: number } {
  const r20 = m[2], r21 = m[6], r22 = m[10];
  const yaw = (Math.asin(Math.max(-1, Math.min(1, -r20))) * 180) / Math.PI;
  const pitch = (Math.atan2(r21, r22) * 180) / Math.PI;
  return { yaw, pitch };
}

export function useCameraPresence({ enabled, videoRef, onSample, onLookAway }: Options) {
  const [state, setState] = useState<CameraState>("off");
  const [gaze, setGaze] = useState<Gaze>("no-face");
  const [error, setError] = useState<string>("");
  const [debug, setDebug] = useState<string>("");
  const debugOn = typeof location !== "undefined" && new URLSearchParams(location.search).has("debug");
  const cb = useRef({ onSample, onLookAway });
  cb.current = { onSample, onLookAway };

  useEffect(() => {
    if (!enabled) {
      setState("off");
      return;
    }
    let cancelled = false;
    let stream: MediaStream | null = null;
    let timer: number | undefined;
    let landmarker: { detectForVideo: (v: HTMLVideoElement, t: number) => any; close: () => void } | null = null;

    (async () => {
      setState("starting");
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { width: 320, height: 240, facingMode: "user" }, audio: false });
      } catch (e) {
        if (!cancelled) {
          setState("denied");
          setError((e as DOMException)?.name === "NotAllowedError" ? "Camera permission was blocked." : "No camera found.");
        }
        return;
      }
      if (cancelled) return stream.getTracks().forEach((t) => t.stop());
      const video = videoRef.current;
      if (!video) return;
      video.srcObject = stream;
      video.muted = true;
      await video.play().catch(() => undefined);

      try {
        const vision = await import("@mediapipe/tasks-vision");
        const wasmBase = (await exists(`${LOCAL_WASM}/vision_wasm_internal.wasm`)) ? LOCAL_WASM : WASM_CDN;
        const modelPath = (await exists(LOCAL_MODEL)) ? LOCAL_MODEL : MODEL_CDN;
        const fileset = await vision.FilesetResolver.forVisionTasks(wasmBase);
        const make = (delegate: "GPU" | "CPU") =>
          vision.FaceLandmarker.createFromOptions(fileset, {
            baseOptions: { modelAssetPath: modelPath, delegate },
            runningMode: "VIDEO",
            numFaces: 1,
            outputFaceBlendshapes: true,
            outputFacialTransformationMatrixes: true,
          });
        landmarker = await make("GPU").catch(() => make("CPU"));
      } catch (e) {
        if (!cancelled) {
          setState("error");
          setError("Could not load the face model (needs internet the first time).");
        }
        console.warn("face landmarker failed", e);
        return;
      }
      if (cancelled) return;

      // calibration baseline: the presenter looks at the camera for ~1.5s
      const base = { yaw: 0, pitch: 0, down: 0, n: 0 };
      setState("calibrating");
      let win = { frames: 0, face_frames: 0, engaged_frames: 0, down_frames: 0, turned_frames: 0 };
      let winStart = performance.now();
      let awaySince: number | null = null;
      let lastGaze: Gaze = "no-face";

      timer = window.setInterval(() => {
        const v = videoRef.current;
        if (!v || v.readyState < 2 || !landmarker) return;
        let res;
        try {
          res = landmarker.detectForVideo(v, performance.now());
        } catch {
          return;
        }
        const now = performance.now();
        const m = res?.facialTransformationMatrixes?.[0]?.data as number[] | undefined;
        const cats = res?.faceBlendshapes?.[0]?.categories as { categoryName: string; score: number }[] | undefined;
        let g: Gaze = "no-face";
        if (m && cats) {
          const { yaw, pitch } = headAngles(m);
          const down = (blend(cats, "eyeLookDownLeft") + blend(cats, "eyeLookDownRight")) / 2;
          const side = Math.max(blend(cats, "eyeLookOutLeft"), blend(cats, "eyeLookOutRight"), blend(cats, "eyeLookInLeft"), blend(cats, "eyeLookInRight"));
          if (base.n < CALIBRATION_FRAMES) {
            base.yaw += yaw; base.pitch += pitch; base.down += down; base.n += 1;
            if (base.n === CALIBRATION_FRAMES) {
              base.yaw /= base.n; base.pitch /= base.n; base.down /= base.n;
              setState("on");
              winStart = now;
            }
            return;
          }
          const dy = Math.abs(yaw - base.yaw);
          const dp = pitch - base.pitch;
          if (debugOn && Math.random() < 0.2)
            setDebug(`yaw ${(yaw - base.yaw).toFixed(0)}° pitch ${dp.toFixed(0)}° down ${(down - base.down).toFixed(2)} side ${side.toFixed(2)}`);
          if (dy > 25 || side > 0.6) g = "turned"; // facing the projected slides / away
          else if (down - base.down > 0.3 || Math.abs(dp) > 18) g = "down"; // notes or laptop screen

          else g = "audience";
        } else if (base.n < CALIBRATION_FRAMES) {
          return;
        }

        win.frames += 1;
        if (g !== "no-face") win.face_frames += 1;
        if (g === "audience") win.engaged_frames += 1;
        if (g === "down") win.down_frames += 1;
        if (g === "turned") win.turned_frames += 1;

        if (g === "audience") {
          if (awaySince !== null) {
            const d = (now - awaySince) / 1000;
            if (d >= LOOK_AWAY_MIN_S) cb.current.onLookAway(d);
          }
          awaySince = null;
        } else if (g !== "no-face" && awaySince === null) {
          awaySince = now;
        }
        if (g !== lastGaze) {
          lastGaze = g;
          setGaze(g);
        }
        if (now - winStart >= REPORT_EVERY_MS && win.frames > 0) {
          cb.current.onSample({ ...win, seconds: (now - winStart) / 1000 });
          win = { frames: 0, face_frames: 0, engaged_frames: 0, down_frames: 0, turned_frames: 0 };
          winStart = now;
        }
      }, 1000 / FPS);
    })();

    return () => {
      cancelled = true;
      window.clearInterval(timer);
      try {
        landmarker?.close();
      } catch {
        /* ignore */
      }
      stream?.getTracks().forEach((t) => t.stop());
      const v = videoRef.current;
      if (v) v.srcObject = null;
      setGaze("no-face");
    };
  }, [enabled, videoRef, debugOn]);

  return { state, gaze, error, debug };
}
