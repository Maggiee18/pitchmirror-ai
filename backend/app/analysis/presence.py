"""Deterministic camera presence metrics.

The browser runs a face landmark model locally and sends only COUNTS (never video): how many frames had a face,
how many the presenter faced the audience/camera, looked down, or turned away. Everything here is arithmetic on
those counts, so every number in the report is measured, and the interpretation is a clearly labelled estimate.
"""
from __future__ import annotations

from dataclasses import dataclass, field

EYE_CONTACT_GOOD = 60.0  # % of face-visible time
EYE_CONTACT_LOW = 40.0
LONG_LOOK_AWAY_S = 6.0

PRESENCE_CAVEAT = (
    "Eye contact is estimated in your browser from head pose and eye direction, relative to a short calibration "
    "while you looked at the camera. No video is uploaded or stored. It is an estimate, not a validated measure."
)


@dataclass
class SlidePresence:
    frames: int = 0
    face_frames: int = 0
    engaged_frames: int = 0
    down_frames: int = 0
    turned_frames: int = 0
    seconds: float = 0.0

    def add(self, s: dict) -> None:
        self.frames += s["frames"]
        self.face_frames += s["face_frames"]
        self.engaged_frames += s["engaged_frames"]
        self.down_frames += s["down_frames"]
        self.turned_frames += s["turned_frames"]
        self.seconds += s["seconds"]

    def pct(self, n: int) -> float | None:
        return round(n / self.face_frames * 100, 1) if self.face_frames >= 10 else None

    def summary(self) -> dict:
        return {
            "seconds": round(self.seconds, 1),
            "face_visible_pct": round(self.face_frames / self.frames * 100, 1) if self.frames else None,
            "eye_contact_pct": self.pct(self.engaged_frames),
            "looking_down_pct": self.pct(self.down_frames),
            "turned_away_pct": self.pct(self.turned_frames),
        }


@dataclass
class PresenceTracker:
    per_slide: dict[int, SlidePresence] = field(default_factory=dict)
    look_aways: list[dict] = field(default_factory=list)

    @property
    def used(self) -> bool:
        return any(p.frames for p in self.per_slide.values())

    def add_sample(self, slide: int, sample: dict) -> None:
        self.per_slide.setdefault(slide, SlidePresence()).add(sample)

    def total(self) -> SlidePresence:
        t = SlidePresence()
        for p in self.per_slide.values():
            t.add({"frames": p.frames, "face_frames": p.face_frames, "engaged_frames": p.engaged_frames,
                   "down_frames": p.down_frames, "turned_frames": p.turned_frames, "seconds": p.seconds})
        return t

    def live(self) -> dict:
        s = self.total().summary()
        return {"camera": self.used, "eye_contact_pct": s["eye_contact_pct"], "face_visible_pct": s["face_visible_pct"]}

    def report(self) -> dict:
        if not self.used:
            return {"used": False}
        t = self.total().summary()
        return {
            "used": True,
            "raw": {
                **t,
                "long_look_aways": len(self.look_aways),
                "longest_look_away_s": round(max((l["duration_s"] for l in self.look_aways), default=0.0), 1),
                "per_slide": {str(k): v.summary() for k, v in sorted(self.per_slide.items())},
            },
            "interpretation": interpret(t, self.look_aways),
            "caveats": [PRESENCE_CAVEAT],
        }


def clean_sample(msg: dict) -> dict | None:
    """Validate a presence message from the client. Returns None if it is nonsense."""
    try:
        s = {k: int(msg.get(k, 0)) for k in ("frames", "face_frames", "engaged_frames", "down_frames", "turned_frames")}
        s["seconds"] = float(msg.get("seconds", 0))
    except (TypeError, ValueError):
        return None
    if not (0 < s["frames"] <= 200 and 0 <= s["seconds"] <= 30):
        return None
    if not (0 <= s["face_frames"] <= s["frames"]):
        return None
    for k in ("engaged_frames", "down_frames", "turned_frames"):
        if not (0 <= s[k] <= s["face_frames"]):
            return None
    return s


def interpret(t: dict, look_aways: list[dict]) -> list[dict]:
    out = []
    ec = t["eye_contact_pct"]
    if ec is None:
        return [{"kind": "improvement", "text": "Your face was rarely visible to the camera, so eye contact could not be estimated.",
                 "suggestion": "Sit so your face is centred and well lit."}]
    if ec >= EYE_CONTACT_GOOD:
        out.append({"kind": "positive", "text": f"Good eye contact: facing the audience about {ec:.0f}% of the time.", "suggestion": ""})
    elif ec < EYE_CONTACT_LOW:
        where = "down (notes or screen)" if (t["looking_down_pct"] or 0) >= (t["turned_away_pct"] or 0) else "away toward the slides"
        out.append({"kind": "improvement", "text": f"Low eye contact: about {ec:.0f}%. You mostly looked {where}.",
                    "suggestion": "Glance at the slide, then deliver the point to the audience."})
    else:
        out.append({"kind": "improvement", "text": f"Moderate eye contact: about {ec:.0f}%.",
                    "suggestion": "Aim to face the audience for most of each explanation."})
    if look_aways:
        out.append({"kind": "improvement",
                    "text": f"{len(look_aways)} stretch(es) of {LONG_LOOK_AWAY_S:.0f}s+ looking away "
                            f"(longest {max(l['duration_s'] for l in look_aways):.0f}s).",
                    "suggestion": "Long look-aways usually mean reading. Know your first sentence for each slide by heart."})
    return out
