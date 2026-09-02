"""Rebuild website_final unimodal DAEP from ckpt['cfg'] (TwinsModel_Wiserep.build_daep).

Architecture follows DAEP by Yunyi Shen and Alex Gagliano:
https://github.com/YunyiShen/Perceiver-diffusion-autoencoder
Copyright (c) 2025 Yunyi Shen. MIT License.
"""

from torch import nn

from astrodash.infrastructure.ml.classifiers.daep.SpectraLayers import (
    spectraTransceiverEncoder,
    spectraTransceiverScore2stages,
)
from astrodash.infrastructure.ml.classifiers.daep.daep import unimodaldaep


class Daepaggregator(nn.Module):
    def __init__(
        self,
        spectraTransceiverEncoder,
        bottleneck_length,
        bottleneck_dim,
        model_dim,
        num_heads,
        num_layers,
        ff_dim,
        dropout,
        selfattn,
        concat,
    ):
        super().__init__()
        self.encoder = spectraTransceiverEncoder(
            bottleneck_length=bottleneck_length,
            bottleneck_dim=bottleneck_dim,
            model_dim=model_dim,
            num_heads=num_heads,
            num_layers=num_layers,
            ff_dim=ff_dim,
            dropout=dropout,
            selfattn=selfattn,
            concat=concat,
        )
        self.MLPEncoder = nn.Sequential(
            nn.Linear(bottleneck_dim, 4 * bottleneck_dim),
            nn.GELU(),
            nn.Linear(4 * bottleneck_dim, bottleneck_dim),
        )
        self.bottleneck_length = bottleneck_length
        self.bottleneck_dim = bottleneck_dim
        self.model_dim = model_dim
        self.num_heads = num_heads
        self.num_layers = num_layers
        self.ff_dim = ff_dim
        self.dropout = dropout
        self.selfattn = selfattn
        self.concat = concat

    def encode_raw(self, x):
        z = self.encoder(x)
        z = self.MLPEncoder(z)
        return z

    def forward(self, x):
        return self.encode_raw(x)


def build_daep(cfg: dict):
    encoder = Daepaggregator(
        spectraTransceiverEncoder=spectraTransceiverEncoder,
        bottleneck_length=cfg["bottleneck_length"],
        bottleneck_dim=cfg["bottleneck_dim"],
        model_dim=cfg["model_dim"],
        num_heads=cfg["num_heads"],
        num_layers=cfg["num_layers"],
        ff_dim=cfg["ff_dim"],
        dropout=cfg["dropout"],
        selfattn=cfg["selfattn"],
        concat=cfg["concat"],
    )
    score = spectraTransceiverScore2stages(
        bottleneck_dim=cfg["bottleneck_dim"],
        model_dim=cfg["model_dim"],
        num_heads=cfg["num_heads"],
        ff_dim=cfg["ff_dim"],
        num_layers=cfg["num_layers"],
        dropout=cfg["dropout"],
        selfattn=cfg["selfattn"],
        concat=cfg["concat"],
        cross_attn_only=cfg["cross_attn_only"],
        hidden_len=cfg["hidden_len"],
    )
    return unimodaldaep(encoder, score, name="flux")
