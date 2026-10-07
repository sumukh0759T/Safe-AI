"""
Voice Bridge backend triage (Module 10, laptop side).

Normal flow: Board 4 firmware listens after the DFPlayer prompt and publishes
voice/reply itself. attempt_voice_triage() is the fallback path used when the
backend must classify a reply clip directly (e.g. Board 4 offline): it calls
the real KWS model (models/voice_kws_model.tflite) when available, otherwise a
documented energy-heuristic so the alert pipeline never dead-ends.
"""
import json
import os
from typing import Optional

MODEL_PATH = os.path.join("models", "voice_kws_model.tflite")
LABELS_PATH = os.path.join("models", "voice_kws_labels.json")

Reply = str


def _heuristic_reply(rms: float, speech_ratio: float) -> Reply:
    """
    Fallback thresholds over Board-4-style features:
      rms          — window RMS energy (0..1 normalized)
      speech_ratio — fraction of sub-windows with speech-band activity
    Tuned for MAX4466 levels; recalibrate after Phase D bring-up.
    """
    if rms < 0.02:
        return "no_response"
    if speech_ratio > 0.35 or rms > 0.45:
        return "help" if rms > 0.45 else "fine"
    return "no_response"


def _run_kws_model(features) -> Optional[Reply]:
    if not os.path.exists(MODEL_PATH):
        return None
    try:
        from tensorflow.lite import Interpreter   # noqa: heavy import guarded
        import numpy as np
        with open(LABELS_PATH) as f:
            labels = json.load(f)
        interp = Interpreter(model_path=MODEL_PATH)
        interp.allocate_tensors()
        inp = interp.get_input_details()[0]
        x = np.asarray(features, dtype=(np.float32 if inp["dtype"] == np.float32
                                        else np.int8))
        if x.ndim == 1:
            x = x[None, ...]
        interp.set_tensor(inp["index"], x)
        interp.invoke()
        out = interp.get_output_details()[0]
        logits = interp.get_tensor(out["index"])
        return str(labels[int(logits.argmax())])
    except Exception as e:
        print(f"[voice_bridge] KWS model failed, falling back: {e}")
        return None


def attempt_voice_triage(features=None, wav_path: Optional[str] = None,
                         sample_rate: int = 16000) -> Reply:
    """
    Classify a spoken reply into fine / help / no_response.

    `features` may be either a raw waveform (list/ndarray) or a precomputed
    feature dict {"rms": float, "speech_ratio": float}.
    """
    if features is not None and not isinstance(features, dict):
        waveform = features
        model_out = _run_kws_model(waveform)
        if model_out:
            return model_out
        # derive heuristic inputs from the waveform using Module 4's extractor
        try:
            import numpy as np
            from datasets.audio_features import extract_features
            wave = np.asarray(waveform, dtype=np.float32)
            rms, zcr, centroid = extract_features(wave, sr=sample_rate)
            # crude speech-likeness: voiced centroid band + low zero crossings
            speech_ratio = float(0.3 < centroid / max(sample_rate / 2, 1) < 0.8
                                 and zcr < 0.2)
            return _heuristic_reply(rms=rms, speech_ratio=speech_ratio)
        except Exception:
            return "no_response"

    feats = features or {}
    model_out = _run_kws_model([feats.get("rms", 0.0), feats.get("speech_ratio", 0.0)])
    if model_out:
        return model_out
    return _heuristic_reply(rms=float(feats.get("rms", 0.0)),
                            speech_ratio=float(feats.get("speech_ratio", 0.0)))
