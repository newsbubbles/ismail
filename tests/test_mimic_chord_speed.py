"""mimic's attack map runs on a numpy STFT, not librosa's (ledger M302): librosa's first stft in a process costs
seconds (lazy imports, numba), and a deck's first bars paid it on their first mimic note. The sound stays the same
to within -100 dB."""
import copy

import numpy as np
import pytest

from ismail import mimic
from ismail.dsp import SR


def _attack_noise_librosa(a, b, w, rng, n, sr, gain, rms_now):
    """mimic._attack_noise as it was, on librosa.stft / istft: the reference."""
    import librosa
    ta = 10 ** (np.array(a['atk_tf']) / 10)
    tb = 10 ** (np.array(b['atk_tf']) / 10)
    var = ta * (1 - w) + tb * w
    ref = 10 ** ((a['atk_ref_db'] * (1 - w) + b['atk_ref_db'] * w) / 20)
    k = (rms_now / ref) ** 2 if ref > 0 else 0.0
    fr = np.fft.rfftfreq(mimic.ATK_NFFT, 1 / sr)
    nh = mimic.NOISE_HZ
    edges = np.concatenate([[nh[0] / 2 ** (1 / 6)], np.sqrt(nh[:-1] * nh[1:]), [nh[-1] * 2 ** (1 / 6)]])
    band = np.clip(np.searchsorted(edges, fr) - 1, 0, len(nh) - 1)
    G = np.sqrt(var[band, :] * k) * gain
    G[fr < 20] = 0
    sh = mimic.ATK_NFFT // (2 * mimic.ATK_HOP)
    need = mimic.ATK_NFFT + mimic.ATK_HOP * (mimic.ATK_FRAMES - 1)
    res = np.zeros((2, n))
    for ch in range(2):
        wn = rng.standard_normal(need + mimic.ATK_NFFT)
        Z = librosa.stft(wn, n_fft=mimic.ATK_NFFT, hop_length=mimic.ATK_HOP, center=True)
        Gc = np.zeros((G.shape[0], Z.shape[1]))
        Gc[:, sh:sh + G.shape[1]] = G[:, :max(0, min(G.shape[1], Z.shape[1] - sh))]
        z = librosa.istft(Z * Gc, hop_length=mimic.ATK_HOP, n_fft=mimic.ATK_NFFT, center=True, length=len(wn))
        m = min(n, need)
        res[ch, :m] = z[:m]
    return res


def residual_db(ref, y):
    return 10 * np.log10(np.sum((ref - y) ** 2) / np.sum(ref ** 2) + 1e-300)


@pytest.fixture(scope='module')
def cello():
    return mimic.load_profile('cello')


@pytest.fixture(scope='module')
def plucked(cello):
    """The cello's notes as a decaying, beating instrument (a harp-like case: prompt and after decays, unison
    strings), so the decaying branch and its beating partials run too."""
    p = copy.deepcopy(cello)
    p['kind'] = 'decaying'
    for nt in p['notes']:
        k = nt['partials']
        nt['t60_prompt'] = list(np.linspace(1.5, 0.2, k))
        nt['t60_after'] = list(np.linspace(6.0, 0.8, k))
        nt['beat_hz'] = list(np.linspace(0.8, 3.0, k))
        nt['beat_db'] = list(np.full(k, 4.0))
    return p


def test_stft_pair_matches_librosa():
    import librosa
    x = np.random.default_rng(1).standard_normal(8960)
    win = mimic._hann(1024)
    Z = mimic._stft(x, 1024, 256, win)
    Zl = librosa.stft(x, n_fft=1024, hop_length=256, center=True)
    assert Z.shape == Zl.T.shape and residual_db(Zl.T, Z) < -200
    G = np.random.default_rng(2).uniform(0, 1, Z.shape)
    y = mimic._istft(Z * G, 1024, 256, win, len(x))
    yl = librosa.istft((Z * G).T, hop_length=256, n_fft=1024, center=True, length=len(x))
    assert residual_db(yl, y) < -200


@pytest.mark.parametrize('which', ['cello', 'plucked'])
def test_a_dense_chord_sounds_as_before(which, request, monkeypatch):
    prof = request.getfixturevalue(which)
    t = np.arange(int(0.6 * SR)) / SR
    chord = [(48 + i * 4, 0.3 + 0.01 * i) for i in range(4)]       # 4 notes, short: the CI machine is shared
    new = [mimic.render(prof, mimic._hz(m), t, 0.7, g, SR) for m, g in chord]
    monkeypatch.setattr(mimic, '_attack_noise', _attack_noise_librosa)
    old = [mimic.render(prof, mimic._hz(m), t, 0.7, g, SR) for m, g in chord]
    for o, y in zip(old, new):
        assert residual_db(o, y) < -100
