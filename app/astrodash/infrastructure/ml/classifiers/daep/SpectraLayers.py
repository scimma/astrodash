"""Spectra encoder/score layers required by website_final unimodaldaep ckpts.

Vendored (inference-only) from DAEP by Yunyi Shen and Alex Gagliano:
https://github.com/YunyiShen/Perceiver-diffusion-autoencoder
Copyright (c) 2025 Yunyi Shen. MIT License.
"""

import torch
from torch import nn

from .Perceiver import PerceiverDecoder2stages, PerceiverEncoder
from .util_layers import MLP, SinusoidalMLPPositionalEmbedding


class spectraEmbedding(nn.Module):
    def __init__(self, model_dim=32, concat=False):
        super(spectraEmbedding, self).__init__()
        self.phase_embd_layer = SinusoidalMLPPositionalEmbedding(model_dim)
        self.concat = concat
        self.wavelength_embd_layer = SinusoidalMLPPositionalEmbedding(model_dim)
        self.flux_embd = nn.Linear(1, model_dim)
        if concat:
            self.spfc = MLP(2 * model_dim, model_dim, [model_dim])

    def forward(self, wavelength, flux, phase):
        if self.concat:
            flux_embd = self.spfc(
                torch.cat(
                    [
                        self.flux_embd(flux[:, :, None]),
                        self.wavelength_embd_layer(wavelength),
                    ],
                    -1,
                )
            )
        else:
            flux_embd = self.flux_embd(flux[:, :, None]) + self.wavelength_embd_layer(
                wavelength
            )
        phase_embd = self.phase_embd_layer(phase[:, None])
        return torch.cat([flux_embd, phase_embd], dim=1)


class spectraTransceiverScore2stages(nn.Module):
    def __init__(
        self,
        bottleneck_dim,
        model_dim=32,
        num_heads=4,
        ff_dim=32,
        num_layers=4,
        dropout=0.1,
        selfattn=False,
        concat=True,
        cross_attn_only=False,
        hidden_len=256,
    ):
        super(spectraTransceiverScore2stages, self).__init__()
        self.decoder = PerceiverDecoder2stages(
            bottleneck_dim,
            hidden_len,
            1,
            model_dim,
            num_heads,
            ff_dim,
            num_layers,
            dropout,
            selfattn,
            cross_attn_only,
        )
        self.spectraEmbd = spectraEmbedding(model_dim, concat)
        self.model_dim = model_dim

    def forward(self, x, bottleneck, aux):
        flux, wavelength, phase, mask = x["flux"], x["wavelength"], x["phase"], x["mask"]
        x = self.spectraEmbd(wavelength, flux, phase)
        if aux is not None:
            aux = torch.cat((x[:, -1, :][:, None, :], aux), axis=1)
        else:
            aux = x[:, -1, :][:, None, :]
        x = x[:, :-1, :]
        return self.decoder(bottleneck, x, aux, mask).squeeze(-1)


class spectraTransceiverEncoder(nn.Module):
    def __init__(
        self,
        bottleneck_length,
        bottleneck_dim,
        model_dim=32,
        num_heads=4,
        num_layers=4,
        ff_dim=32,
        dropout=0.1,
        selfattn=False,
        concat=True,
    ):
        super(spectraTransceiverEncoder, self).__init__()
        self.encoder = PerceiverEncoder(
            bottleneck_length,
            bottleneck_dim,
            model_dim,
            num_heads,
            num_layers,
            ff_dim,
            dropout,
            selfattn,
        )
        self.spectraEmbd = spectraEmbedding(model_dim, concat)
        self.model_dim = model_dim
        self.bottleneck_length = bottleneck_length
        self.bottleneck_dim = bottleneck_dim

    def forward(self, x):
        flux, wavelength, phase, mask = x["flux"], x["wavelength"], x["phase"], x["mask"]
        x = self.spectraEmbd(wavelength, flux, phase)
        if mask is not None:
            mask = torch.cat(
                [mask, torch.zeros(mask.shape[0], 1).bool().to(mask.device)], dim=1
            )
        x = self.encoder(x, mask)
        return x
