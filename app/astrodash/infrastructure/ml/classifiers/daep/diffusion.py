"""Diffusion buffers required to load unimodaldaep state_dict strictly.

Sampling/training loops from upstream DAEP are omitted; encode() does not use them.

Vendored (inference-only) from DAEP by Yunyi Shen and Alex Gagliano:
https://github.com/YunyiShen/Perceiver-diffusion-autoencoder
Copyright (c) 2025 Yunyi Shen. MIT License.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F


class GaussianDiffusionTrainer(nn.Module):
    def __init__(self, beta_1=1e-4, beta_T=0.02, T=1000):
        super().__init__()
        self.T = T
        self.register_buffer("betas", torch.linspace(beta_1, beta_T, T).float())
        alphas = 1.0 - self.betas
        alphas_bar = torch.cumprod(alphas, dim=0)
        self.register_buffer("sqrt_alphas_bar", torch.sqrt(alphas_bar))
        self.register_buffer("sqrt_one_minus_alphas_bar", torch.sqrt(1.0 - alphas_bar))


class GaussianDiffusionSampler(nn.Module):
    def __init__(self, beta_1, beta_T, T, mean_type="epsilon", var_type="fixedsmall"):
        super().__init__()
        self.T = T
        self.mean_type = mean_type
        self.var_type = var_type
        self.register_buffer("betas", torch.linspace(beta_1, beta_T, T).float())
        alphas = 1.0 - self.betas
        alphas_bar = torch.cumprod(alphas, dim=0)
        alphas_bar_prev = F.pad(alphas_bar, [1, 0], value=1)[:T]
        self.register_buffer("sqrt_recip_alphas_bar", torch.sqrt(1.0 / alphas_bar))
        self.register_buffer("sqrt_recipm1_alphas_bar", torch.sqrt(1.0 / alphas_bar - 1))
        self.register_buffer(
            "posterior_var",
            self.betas * (1.0 - alphas_bar_prev) / (1.0 - alphas_bar),
        )
        self.register_buffer(
            "posterior_log_var_clipped",
            torch.log(torch.cat([self.posterior_var[1:2], self.posterior_var[1:]])),
        )
        self.register_buffer(
            "posterior_mean_coef1",
            torch.sqrt(alphas_bar_prev) * self.betas / (1.0 - alphas_bar),
        )
        self.register_buffer(
            "posterior_mean_coef2",
            torch.sqrt(alphas) * (1.0 - alphas_bar_prev) / (1.0 - alphas_bar),
        )
