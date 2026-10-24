"""What happens to a voice before the detector hears it, simulated on the GPU a batch at a time.

Each clip gets one of four treatments, chosen without looking at its label:

    clean   the file as it is (an uploaded recording, tab audio)
    phone   a narrow-band line: 300-3400 Hz, companding, a little line noise
    air     a loudspeaker in a room picked up by a microphone: the speaker's distortion and colour,
            the room's echo, the room's noise, the microphone's colour (optionally after a phone line)
    noise   background noise only

plus, for any of them, a random low-pass (recordings come in many bandwidths), dropouts and
clipping. The echoes come from measured rooms when a bank of impulse responses is given, and
from a statistical room model otherwise; the noise is recorded noise, other people talking,
mains hum or coloured hiss.
"""
from __future__ import annotations

import math

import torch

SR = 16000


class Augmenter:
    def __init__(self, device, rirs: torch.Tensor | None = None, noises: torch.Tensor | None = None,
                 p_clean: float = 0.22, p_phone: float = 0.16, p_air: float = 0.47, p_measured: float = 0.6,
                 extras: bool = True, seed: int = 0):
        """rirs: (count, taps) measured impulse responses, direct sound at tap 0.
        noises: (count, samples) recorded noise. Both optional."""
        self.device = device
        self.rirs = rirs.to(device) if rirs is not None and len(rirs) else None
        self.noises = noises.to(device) if noises is not None and len(noises) else None
        self.p = (p_clean, p_phone, p_air)
        self.p_measured, self.extras = p_measured, extras
        self.g = torch.Generator(device=device).manual_seed(seed)

    # -- random numbers ---------------------------------------------------------------------------
    def u(self, n: int, lo: float, hi: float) -> torch.Tensor:
        return torch.rand(n, 1, device=self.device, generator=self.g) * (hi - lo) + lo

    def logu(self, n: int, lo: float, hi: float) -> torch.Tensor:
        return torch.exp(self.u(n, math.log(lo), math.log(hi)))

    def coin(self, n: int, p: float) -> torch.Tensor:
        return torch.rand(n, 1, device=self.device, generator=self.g) < p

    def randn(self, *shape) -> torch.Tensor:
        return torch.randn(*shape, device=self.device, generator=self.g)

    # -- building blocks ----------------------------------------------------------------------------
    @staticmethod
    def rms(x: torch.Tensor) -> torch.Tensor:
        return x.pow(2).mean(-1, keepdim=True).sqrt().clamp_min(1e-5)

    def filt(self, x: torch.Tensor, response) -> torch.Tensor:
        """Zero-phase filter: response(freqs in Hz, shape (1, bins)) -> gains (batch, bins)."""
        n = x.shape[-1]
        freqs = torch.fft.rfftfreq(n, 1 / SR, device=x.device)[None]
        return torch.fft.irfft(torch.fft.rfft(x) * response(freqs), n=n)

    @staticmethod
    def lowpass(f, cut, order=4):
        return 1 / torch.sqrt(1 + (f / cut) ** (2 * order))

    @staticmethod
    def highpass(f, cut, order=2):
        return 1 / torch.sqrt(1 + (cut / f.clamp_min(1.0)) ** (2 * order))

    def colour(self, f, n: int, strength: float = 6.0):
        """A device's frequency response: a tilt and two broad bumps or dips, in dB."""
        octave = torch.log2(f.clamp_min(50.0) / 1000.0)
        db = self.u(n, -strength / 3, strength / 3) * octave
        for _ in range(2):
            centre, width = self.logu(n, 200, 6000), self.u(n, 0.4, 1.5)
            db = db + self.u(n, -strength, strength) * torch.exp(-0.5 * ((octave - torch.log2(centre / 1000.0)) / width) ** 2)
        return 10 ** (db / 20)

    def synthetic_rirs(self, n: int, taps: int = 9600) -> torch.Tensor:
        """Statistical rooms: a direct sound, a few early reflections and a decaying diffuse tail
        whose high frequencies die faster, as in furnished rooms."""
        t = torch.arange(taps, device=self.device)[None] / SR
        rt60 = self.logu(n, 0.15, 0.9)
        tail = self.randn(n, taps) * torch.exp(-6.91 * t / rt60)
        dark = torch.exp(-6.91 * t / (rt60 * self.u(n, 0.35, 0.8)))            # the treble's shorter decay
        bright = self.filt(tail, lambda f: self.highpass(f, 2500.0, 1))
        tail = (tail - bright) + bright * dark / torch.exp(-6.91 * t / rt60).clamp_min(1e-6)
        tail[:, :32] = 0                                                           # nothing before ~2 ms
        tail = tail / self.rms(tail) / math.sqrt(taps)                             # unit energy
        rir = tail * 10 ** (-self.u(n, -2.0, 16.0) / 20)                           # direct-to-reverberant ratio, dB
        for _ in range(4):
            delay = (self.logu(n, 0.002, 0.04) * SR).long()
            rir.scatter_add_(1, delay, self.u(n, -0.5, 0.5))
        rir[:, 0] += 1.0
        return rir

    def pick_rirs(self, n: int, taps: int = 9600) -> torch.Tensor:
        rir = self.synthetic_rirs(n, taps)
        if self.rirs is not None:
            index = torch.randint(len(self.rirs), (n,), device=self.device, generator=self.g)
            real = self.rirs[index][:, :taps]
            if real.shape[1] < taps:
                real = torch.nn.functional.pad(real, (0, taps - real.shape[1]))
            rir = torch.where(self.coin(n, self.p_measured), real, rir)
        return rir

    def reverb(self, x: torch.Tensor, rir: torch.Tensor) -> torch.Tensor:
        n = x.shape[-1] + rir.shape[-1]
        return torch.fft.irfft(torch.fft.rfft(x, n=n) * torch.fft.rfft(rir, n=n), n=n)[:, : x.shape[-1]]

    def noise(self, x: torch.Tensor) -> torch.Tensor:
        """Unit-RMS background noise for each clip: recorded noise, babble, hum or hiss."""
        n, t = x.shape
        hiss = self.filt(self.randn(n, t), lambda f: (f.clamp_min(20.0) / 1000.0) ** (-self.u(n, 0.0, 1.2))
                         * self.lowpass(f, self.logu(n, 800, 8000), 2))
        phase = self.u(n, 0, 6.28)
        base = torch.where(self.coin(n, 0.5), 50.0, 60.0)
        time = torch.arange(t, device=self.device)[None] / SR
        hum = sum(self.u(n, 0.1, 1.0) * torch.sin(2 * math.pi * base * k * time + phase * k) for k in (1, 2, 3, 5))
        babble = sum(x[torch.randperm(n, device=self.device, generator=self.g)].roll(int(torch.randint(t, (1,), generator=self.g, device=self.device)), -1)
                     for _ in range(3))
        babble = self.reverb(babble, self.synthetic_rirs(n, 4800))
        kind = torch.rand(n, 1, device=self.device, generator=self.g)
        out = torch.where(kind < 0.30, hiss / self.rms(hiss), babble / self.rms(babble))
        out = torch.where(kind > 0.88, hum / self.rms(hum) * 0.7 + hiss / self.rms(hiss) * 0.3, out)
        if self.noises is not None:
            index = torch.randint(len(self.noises), (n,), device=self.device, generator=self.g)
            start = torch.randint(self.noises.shape[1] - t, (n,), device=self.device, generator=self.g)
            picked = self.noises[index[:, None], start[:, None] + torch.arange(t, device=self.device)[None]]
            out = torch.where((kind > 0.30) & (kind < 0.72), picked / self.rms(picked), out)
        return out / self.rms(out)

    def add_noise(self, x: torch.Tensor, snr_db: torch.Tensor) -> torch.Tensor:
        return x + self.noise(x) * self.rms(x) * 10 ** (-snr_db / 20)

    def phone_line(self, x: torch.Tensor) -> torch.Tensor:
        n = len(x)
        lo, hi = self.u(n, 180, 420), self.u(n, 3000, 3800)
        y = self.filt(x, lambda f: self.highpass(f, lo, 3) * self.lowpass(f, hi, 6) * self.colour(f, n, 4.0))
        peak = y.abs().amax(-1, keepdim=True).clamp_min(1e-5)
        z = y / peak * self.u(n, 0.3, 0.95)
        mu = 255.0                                                            # G.711: 8-bit companded samples
        code = torch.round((torch.sign(z) * torch.log1p(mu * z.abs()) / math.log1p(mu)) * 127.5) / 127.5
        z = torch.sign(code) * torch.expm1(code.abs() * math.log1p(mu)) / mu
        y = torch.where(self.coin(n, 0.5), z * peak, y)
        return torch.where(self.coin(n, 0.5), self.add_noise(y, self.u(n, 22, 40)), y)

    def air(self, x: torch.Tensor) -> torch.Tensor:
        n = len(x)
        peak = x.abs().amax(-1, keepdim=True).clamp_min(1e-5)
        drive = self.logu(n, 0.4, 3.0)                                        # a small speaker pushed hard distorts
        y = torch.tanh(drive * x / peak) / torch.tanh(drive) * peak
        lo, hi = self.logu(n, 90, 450), self.logu(n, 4500, 7800)
        y = self.filt(y, lambda f: self.highpass(f, lo, 2) * self.lowpass(f, hi, 3) * self.colour(f, n))
        y = self.reverb(y, self.pick_rirs(n))
        y = self.add_noise(y, self.u(n, 6, 36))
        mic_lo = self.logu(n, 60, 250)
        y = self.filt(y, lambda f: self.highpass(f, mic_lo, 2) * self.colour(f, n, 5.0))
        return y + self.randn(*y.shape) * self.rms(y) * 10 ** (-self.u(n, 35, 60) / 20)   # the microphone's own hiss

    # -- the whole thing -------------------------------------------------------------------------------
    @torch.no_grad()
    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        """x: (batch, samples), any scale. Returns the same shape, unit RMS."""
        n = len(x)
        x = x - x.mean(-1, keepdim=True)
        x = x / self.rms(x)
        r = torch.rand(n, 1, device=self.device, generator=self.g)
        a, b, c = self.p[0], self.p[0] + self.p[1], self.p[0] + self.p[1] + self.p[2]
        phone = self.phone_line(x)
        aired = self.air(torch.where(self.coin(n, 0.3), phone, x))
        noisy = self.add_noise(x, self.u(n, 3, 30))
        y = torch.where(r < a, x, torch.where(r < b, phone, torch.where(r < c, aired, noisy)))
        if not self.extras:
            return torch.nan_to_num(y) / self.rms(y)
        cut = self.logu(n, 3200, 7900)                                         # the recording's bandwidth
        y = torch.where(self.coin(n, 0.35), self.filt(y, lambda f: self.lowpass(f, cut, 8)), y)
        peak = y.abs().amax(-1, keepdim=True)
        y = torch.where(self.coin(n, 0.08), y.clamp(-peak * self.u(n, 0.25, 0.8), peak * self.u(n, 0.25, 0.8)), y)
        gaps = (torch.rand(n, y.shape[-1] // 320 + 1, device=self.device, generator=self.g) < 0.03).repeat_interleave(320, 1)[:, : y.shape[-1]]
        y = torch.where(self.coin(n, 0.06) & gaps, torch.zeros_like(y), y)       # lost packets
        y = torch.nan_to_num(y)
        return y / self.rms(y)
