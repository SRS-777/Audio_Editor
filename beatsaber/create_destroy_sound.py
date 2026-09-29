"""
Creates a Beat Saber style destroy sound effect.
Sharp, clean slice sound with satisfying feedback.
Requires: pip install numpy scipy
"""
import numpy as np
from scipy.io import wavfile

def create_destroy_sound(filename="destroy.wav", duration=0.25, sample_rate=44100):
    """
    Creates a Beat Saber style 'slice' sound effect.
    Combines:
    - Sharp transient (blade impact)
    - High-frequency sweep (slice through)
    - Short resonance (satisfying feedback)
    """
    t = np.linspace(0, duration, int(sample_rate * duration))
    
    # 1. Sharp initial transient (the impact)
    impact_duration = 0.01
    impact_samples = int(sample_rate * impact_duration)
    impact = np.random.uniform(-1, 1, impact_samples)
    impact_env = np.exp(-np.linspace(0, 10, impact_samples))
    impact = impact * impact_env * 0.5
    
    # 2. High frequency sweep (the slice)
    # Sweeps from high to low very quickly
    start_freq = 4000
    end_freq = 1200
    sweep_duration = 0.08
    sweep_samples = int(sample_rate * sweep_duration)
    t_sweep = np.linspace(0, sweep_duration, sweep_samples)
    
    # Exponential frequency sweep
    freq_curve = start_freq * np.exp(-t_sweep * 15)
    phase = 2 * np.pi * np.cumsum(freq_curve) / sample_rate
    sweep = np.sin(phase)
    
    # Sharp envelope for the sweep
    sweep_env = np.exp(-t_sweep * 25)
    sweep = sweep * sweep_env * 0.4
    
    # 3. Brief resonance (satisfying tone)
    resonance_freq = 800
    resonance_duration = 0.15
    resonance_samples = int(sample_rate * resonance_duration)
    t_res = np.linspace(0, resonance_duration, resonance_samples)
    
    resonance = np.sin(2 * np.pi * resonance_freq * t_res)
    resonance += 0.3 * np.sin(2 * np.pi * resonance_freq * 2 * t_res)  # Harmonic
    resonance_env = np.exp(-t_res * 12)
    resonance = resonance * resonance_env * 0.25
    
    # 4. Subtle noise layer (air displacement)
    noise = np.random.uniform(-1, 1, len(t))
    noise_env = np.exp(-t * 30)
    noise = noise * noise_env * 0.15
    
    # Combine all components
    sound = np.zeros(len(t))
    
    # Add impact at the start
    sound[:impact_samples] += impact
    
    # Add sweep slightly after impact
    sweep_start = int(0.005 * sample_rate)
    sound[sweep_start:sweep_start + sweep_samples] += sweep
    
    # Add resonance starting with the sweep
    sound[sweep_start:sweep_start + resonance_samples] += resonance
    
    # Add noise throughout
    sound += noise
    
    # Apply master envelope (quick fade out)
    master_env = np.exp(-t * 8)
    sound = sound * master_env
    
    # Normalize to prevent clipping
    sound = sound / np.max(np.abs(sound)) * 0.85
    
    # Convert to 16-bit integer format
    sound_int = (sound * 32767).astype(np.int16)
    
    wavfile.write(filename, sample_rate, sound_int)
    print(f"Created {filename} - Beat Saber style slice sound ({duration}s)")

if __name__ == "__main__":
    create_destroy_sound()

