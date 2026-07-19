from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Sequence
import random
from .analyzer import SceneAnalysis

@dataclass(frozen=True)
class MotionPlan:
    start: float; end: float
    start_zoom: float; end_zoom: float
    start_x: float; start_y: float; end_x: float; end_y: float
    preset: str; confidence: float; focus_x: float; focus_y: float
    hold_fraction: float = 0.0
    visual_intent: str = "medium"
    narrative_intent: str = "development"
    guidance_reason: str = "Motion Engine fallback."
    def to_dict(self): return asdict(self)

def _ease(x: float)->float:
    x=max(0.0,min(1.0,x)); return x*x*x*(x*(x*6-15)+10)

def build_motion_plan(
    scenes: list[SceneAnalysis],
    cfg: dict[str, Any],
    visual_guidance: Sequence[Any] | None = None,
) -> list[MotionPlan]:
    if visual_guidance is not None and len(visual_guidance) != len(scenes):
        raise ValueError("Visual-to-Motion contract violation: scene and guidance counts differ.")
    rng=random.Random(int(cfg.get('seed',300)))
    min_zoom=max(1.0,float(cfg.get('min_zoom',1.015)))
    max_zoom=max(min_zoom,float(cfg.get('max_zoom',1.105)))
    pan_strength=max(0.0,min(1.0,float(cfg.get('pan_strength',0.72))))
    plans=[]; last=''
    for i,s in enumerate(scenes):
        dur=max(0.1,s.end-s.start)
        safe_x_min=max(0.0,min(0.49,float(cfg.get('safe_focus_x_min',0.20))))
        safe_x_max=max(safe_x_min+0.01,min(1.0,float(cfg.get('safe_focus_x_max',0.80))))
        safe_y_min=max(0.0,min(0.49,float(cfg.get('safe_focus_y_min',0.18))))
        safe_y_max=max(safe_y_min+0.01,min(1.0,float(cfg.get('safe_focus_y_max',0.76))))
        fx=max(safe_x_min,min(safe_x_max,s.focus_x)); fy=max(safe_y_min,min(safe_y_max,s.focus_y))
        tx=(fx-0.5)*2*pan_strength; ty=(fy-0.5)*2*pan_strength
        guide = visual_guidance[i] if visual_guidance is not None else None
        # 4.2 guidance selects behavior; subject analysis still owns the safe focal point.
        if guide is not None:
            if int(guide.scene_index) != i:
                raise ValueError("Visual-to-Motion contract violation: guidance order drifted.")
            preset = str(guide.preferred_preset)
        elif s.face_count>0:
            preset='subject_push_in' if s.subject_scale < 0.20 else 'portrait_hold'
        elif s.confidence < 0.34:
            preset='safe_push_in'
        elif abs(tx)>0.24 or abs(ty)>0.24:
            preset='focus_reveal'
        else:
            preset=rng.choice(['documentary_float','slow_pull_out','safe_push_in'])
        if guide is None and preset==last and preset not in {'subject_push_in','portrait_hold'}:
            preset='slow_pull_out' if preset!='slow_pull_out' else 'documentary_float'
        last=preset
        strength=min(1.0,max(0.35,dur/5.0))*max(0.45,s.confidence)
        if guide is not None:
            strength *= max(0.35, min(1.0, float(guide.intensity)))
        if preset=='subject_push_in':
            z0=min_zoom; z1=min(max_zoom,min_zoom+(max_zoom-min_zoom)*(0.72+0.20*strength)); x0,y0=tx*0.35,ty*0.35; x1,y1=tx,ty
        elif preset=='portrait_hold':
            z0=min_zoom+(max_zoom-min_zoom)*0.35; z1=z0+(max_zoom-min_zoom)*0.18; x0,y0=tx*0.75,ty*0.75; x1,y1=tx,ty
        elif preset=='focus_reveal':
            z0=min_zoom+(max_zoom-min_zoom)*0.55; z1=min_zoom+(max_zoom-min_zoom)*0.72; x0,y0=-tx*0.35,-ty*0.35; x1,y1=tx,ty
        elif preset=='slow_pull_out':
            z0=min_zoom+(max_zoom-min_zoom)*0.78; z1=min_zoom+(max_zoom-min_zoom)*0.22; x0,y0=tx,ty; x1,y1=tx*0.45,ty*0.45
        elif preset=='documentary_float':
            z0=min_zoom+(max_zoom-min_zoom)*0.40; z1=min_zoom+(max_zoom-min_zoom)*0.58; jitter=0.12*pan_strength; x0,y0=tx-rng.uniform(-jitter,jitter),ty-rng.uniform(-jitter,jitter); x1,y1=tx+rng.uniform(-jitter,jitter),ty+rng.uniform(-jitter,jitter)
        else:
            z0=min_zoom; z1=min_zoom+(max_zoom-min_zoom)*0.55; x0,y0=0.0,0.0; x1,y1=tx*0.7,ty*0.7
        # Scene continuity: avoid a visible snap from the previous scene's end zoom
        # back to the new preset's default start zoom. At high zoom, reverse the
        # direction instead of stacking repeated push-ins against max_zoom.
        continuity = bool(cfg.get("scene_continuity", True))
        if continuity and plans:
            previous = plans[-1]
            carried_zoom = previous.end_zoom
            zoom_span = max(0.0001, max_zoom - min_zoom)
            zoom_level = (carried_zoom - min_zoom) / zoom_span
            wants_push = z1 >= z0
            wants_pull = z1 < z0

            if wants_push and zoom_level >= float(cfg.get("reverse_to_pull_above", 0.72)):
                preset = "continuity_pull_out"
                z0 = carried_zoom
                z1 = max(min_zoom + zoom_span * 0.24, carried_zoom - zoom_span * (0.42 + 0.16 * strength))
                x0, y0 = tx * 0.85, ty * 0.85
                x1, y1 = tx * 0.45, ty * 0.45
            elif wants_pull and zoom_level <= float(cfg.get("reverse_to_push_below", 0.28)):
                preset = "continuity_push_in"
                z0 = carried_zoom
                z1 = min(max_zoom, carried_zoom + zoom_span * (0.42 + 0.16 * strength))
                x0, y0 = tx * 0.45, ty * 0.45
                x1, y1 = tx * 0.85, ty * 0.85
            else:
                z0 = carried_zoom

            # Pan coordinates belong to the old image and have no spatial meaning
            # after a hard cut. Start the new image from a neutral crop, then ease
            # toward its own detected subject. This prevents horizontal/vertical
            # snap even when adjacent images have subjects on opposite sides.
            if bool(cfg.get("neutral_pan_on_cut", True)):
                x0, y0 = 0.0, 0.0
            else:
                blend = max(0.0, min(1.0, float(cfg.get("new_scene_pan_start_blend", 0.25))))
                x0 = x0 * blend
                y0 = y0 * blend

        clamp=lambda v:max(-1.0,min(1.0,v))
        hold = max(0.0, min(0.25, float(cfg.get("cut_settle_fraction", 0.07)))) if i > 0 else 0.0
        if guide is not None:
            hold = max(hold, max(0.0, min(0.25, float(guide.hold_fraction))))
        plans.append(MotionPlan(
            s.start,s.end,z0,z1,clamp(x0),clamp(y0),clamp(x1),clamp(y1),
            preset,s.confidence,fx,fy,hold,
            str(guide.visual_intent) if guide is not None else "medium",
            str(guide.narrative_intent) if guide is not None else "development",
            str(guide.reason) if guide is not None else "Motion Engine fallback.",
        ))
    return plans

def motion_state(plans:list[MotionPlan],t:float,hint:int, transition_delay:float=0.0):
    while hint+1<len(plans) and t>=plans[hint].end: hint+=1
    p=plans[min(hint,len(plans)-1)]
    # Keep the incoming camera fully settled while the visual dissolve is active.
    # Without this delay, the new scene advances behind the dissolve and appears
    # to snap/correct itself immediately after the transition.
    delay = max(0.0, min(max(0.0, p.end-p.start-0.05), transition_delay if p.start > 0 else 0.0))
    raw=(t-p.start-delay)/max(0.001,p.end-p.start-delay)
    hold=max(0.0,min(0.25,p.hold_fraction))
    u=0.0 if raw <= hold else _ease((raw-hold)/max(0.001,1.0-hold))
    lerp=lambda a,b:a+(b-a)*u
    return lerp(p.start_zoom,p.end_zoom),lerp(p.start_x,p.end_x),lerp(p.start_y,p.end_y),hint
