"""Phase-off forward methods for the frozen WISeREP DAEP checkpoints."""

import types

import torch


def no_phase_spectra_embed(spectra_embd, wavelength, flux):
    if spectra_embd.concat:
        return spectra_embd.spfc(
            torch.cat(
                [
                    spectra_embd.flux_embd(flux[:, :, None]),
                    spectra_embd.wavelength_embd_layer(wavelength),
                ],
                dim=-1,
            )
        )
    return (
        spectra_embd.flux_embd(flux[:, :, None])
        + spectra_embd.wavelength_embd_layer(wavelength)
    )


def patch_model_no_phase(model):
    """Match the phase-off forward methods used during WISeREP training."""
    patched = {"encoder": 0, "score": 0}

    def encoder_forward_no_phase(self, x):
        flux = x["flux"]
        wavelength = x["wavelength"]
        mask = x["mask"]
        tokens = no_phase_spectra_embed(self.spectraEmbd, wavelength, flux)
        return self.encoder(tokens, mask)

    def score_forward_no_phase(self, x, bottleneck, aux):
        flux = x["flux"]
        wavelength = x["wavelength"]
        mask = x["mask"]
        tokens = no_phase_spectra_embed(self.spectraEmbd, wavelength, flux)
        return self.decoder(bottleneck, tokens, aux, mask).squeeze(-1)

    for module in model.modules():
        cls = module.__class__.__name__
        if cls in {"spectraTransceiverEncoder", "spectraTransceiverEncoder2stages"}:
            module.forward = types.MethodType(encoder_forward_no_phase, module)
            patched["encoder"] += 1
        if cls in {"spectraTransceiverScore", "spectraTransceiverScore2stages"}:
            module.forward = types.MethodType(score_forward_no_phase, module)
            patched["score"] += 1

    print("[phase OFF] patched DAEP to remove phase token entirely")
    print(f"[phase OFF] patched encoder modules: {patched['encoder']}")
    print(f"[phase OFF] patched score modules  : {patched['score']}")

    if patched["encoder"] == 0 or patched["score"] == 0:
        raise RuntimeError(
            "Could not reproduce the phase-off architecture from the training run. "
            "Encoder/score patch targets were not both found."
        )

    return model
