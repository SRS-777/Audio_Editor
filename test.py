import numpy as np, soundfile as sf
SR = 44100
seg = 2.0
t = np.arange(int(seg*SR))/SR

def tone(f, amp):
    sig = amp * np.sin(2*np.pi*f*t)
    fade = int(0.05*SR)
    sig[:fade] *= np.linspace(0,1,fade)
    sig[-fade:] *= np.linspace(1,0,fade)
    return sig

out = np.concatenate([tone(80, 0.5), tone(800, 0.5), tone(5000, 0.3)])
sf.write("eq_tones.wav", out.astype(np.float32), SR)
print("Wrote eq_tones.wav")