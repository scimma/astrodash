"""Minimal unimodaldaep matching website_final checkpoint parameter names.

Vendored (inference-only) from DAEP by Yunyi Shen and Alex Gagliano:
https://github.com/YunyiShen/Perceiver-diffusion-autoencoder
Copyright (c) 2025 Yunyi Shen. MIT License.
"""

import torch.nn as nn

from .diffusion import GaussianDiffusionSampler, GaussianDiffusionTrainer
from .util_layers import SinusoidalMLPPositionalEmbedding


class unimodaldaep(nn.Module):
    def __init__(
        self,
        encoder,
        score,
        name="flux",
        beta_1=1e-4,
        beta_T=0.02,
        T=1000,
    ):
        super().__init__()
        self.encoder = encoder
        self.score_model = score
        self.diffusion_time_embd = SinusoidalMLPPositionalEmbedding(score.model_dim)
        self.diffusion_trainer = GaussianDiffusionTrainer(beta_1, beta_T, T)
        self.diffusion_sampler = GaussianDiffusionSampler(beta_1, beta_T, T)
        self.latent_len = encoder.bottleneck_length
        self.latent_dim = encoder.bottleneck_dim
        self.name = name

    def encode(self, x):
        return self.encoder(x)

    def forward(self, x):
        return self.encode(x)
