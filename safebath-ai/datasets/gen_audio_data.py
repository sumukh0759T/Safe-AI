import numpy as np
import os
try:
    from .audio_features import SR, extract_features
except Exception:
    from audio_features import SR, extract_features
COLUMNS = ['rms','zcr','centroid','label']
CLASS_NAMES = ['silence','background_noise','impact_thud','cry_or_scream','ordinary_speech','water_sound']

def _lowpass(x, alpha):
    y = np.empty_like(x); acc = 0.0
    for i, v in enumerate(x):
        acc += alpha*(v-acc); y[i]=acc
    return y

def make_clip(label, rng):
    n = SR; t = np.arange(n)/SR
    if label == 0:
        return (rng.normal(0,0.006,n)).astype(np.float32)
    if label == 1:
        noise = rng.normal(0,1,n); hiss = _lowpass(noise,0.1); hiss -= hiss.mean()
        mod = 0.6+0.4*np.abs(np.sin(2*np.pi*rng.uniform(0.5,2)*t))
        return ((hiss/(np.abs(hiss).max()+1e-9))*mod*rng.uniform(0.15,0.4)).astype(np.float32)
    if label == 2:
        f = rng.uniform(60,140); tau = rng.uniform(0.04,0.1)
        start = int(rng.uniform(0.05,0.12)*SR); env = np.exp(-t/tau)*(t>=t[start])
        thud = np.sin(2*np.pi*f*t+rng.uniform(0,6))*env
        thud += _lowpass(rng.normal(0,1,n),0.05)*0.25*env
        peak = np.abs(thud).max()+1e-9
        return ((thud/peak)*rng.uniform(0.7,1.0)).astype(np.float32)
    if label == 3:
        f0 = rng.uniform(350,600); wob = 1+0.12*np.sin(2*np.pi*rng.uniform(4,9)*t)
        sig = 0
        for h in (1,2,3,4):
            sig += np.sin(2*np.pi*f0*wob*h*t+rng.uniform(0,6))/h
        syllables = rng.uniform(3,6)
        gate = (np.sin(2*np.pi*syllables*t-np.pi/2)>-0.3).astype(float)
        gate = _lowpass(gate,0.15); sig = sig*gate
        return ((sig/(np.abs(sig).max()+1e-9))*rng.uniform(0.8,1.0)).astype(np.float32)
    if label == 4:
        f0 = rng.uniform(120,250); wob = 1+0.06*np.sin(2*np.pi*rng.uniform(2,5)*t)
        sig = 0
        for h in (1,2,3):
            sig += np.sin(2*np.pi*f0*wob*h*t+rng.uniform(0,6))/h
        syllables = rng.uniform(2,4)
        gate = (np.sin(2*np.pi*syllables*t-np.pi/2)>-0.2).astype(float)
        gate = _lowpass(gate,0.2); sig = sig*gate
        return ((sig/(np.abs(sig).max()+1e-9))*rng.uniform(0.4,0.8)).astype(np.float32)
    noise = rng.normal(0,1,n); hiss = _lowpass(noise,0.25); hiss -= hiss.mean()
    mod = 0.55+0.45*np.abs(np.sin(2*np.pi*rng.uniform(2,6)*t))
    return ((hiss/(np.abs(hiss).max()+1e-9))*mod*rng.uniform(0.5,0.9)).astype(np.float32)

def generate(n_per_class=300, seed=42):
    rng = np.random.default_rng(seed)
    waves = np.concatenate([[make_clip(l,rng) for _ in range(n_per_class)] for l in range(6)])
    labels = np.repeat([0,1,2,3,4,5], n_per_class)
    return waves.astype(np.float32), labels.astype(np.int64)

def main(out_dir='data/synthetic'):
    os.makedirs(out_dir, exist_ok=True)
    waves, labels = generate()
    np.save(os.path.join(out_dir,'audio_synth_waves.npy'), waves)
    np.save(os.path.join(out_dir,'audio_synth_labels.npy'), labels)
    feats = [extract_features(w) for w in waves]
    import pandas as pd
    df = pd.DataFrame(feats, columns=COLUMNS[:3]); df['label']=labels
    df.to_csv(os.path.join(out_dir,'audio_synth_features.csv'), index=False)
    print('ok')

if __name__=='__main__':
    main()
