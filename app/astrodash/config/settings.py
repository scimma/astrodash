from pydantic_settings import BaseSettings
from pydantic import Field, AnyUrl, field_validator, model_validator
from typing import Any, Optional, List, Dict
import os


class Settings(BaseSettings):
    # General
    app_name: str = Field("AstroDash API", env="ASTRODASH_APP_NAME")
    environment: str = Field("production", env="ASTRODASH_ENVIRONMENT")
    debug: bool = Field(False, env="ASTRODASH_DEBUG")

    # API
    api_prefix: str = Field("/api/v1", env="ASTRODASH_API_PREFIX")
    allowed_hosts: List[str] = Field(["*"], env="ASTRODASH_ALLOWED_HOSTS")  # Allow all hosts for API usage
    cors_origins: List[str] = Field(["*"], env="ASTRODASH_CORS_ORIGINS")    # Allow all origins for API usage

    # Security Settings
    secret_key: str = Field("your-super-secret-key-here-make-it-very-long-and-secure-32-chars-min",
                            env="ASTRODASH_SECRET_KEY")
    access_token_expire_minutes: int = Field(60 * 24, env="ASTRODASH_ACCESS_TOKEN_EXPIRE_MINUTES")

    # Rate Limiting
    rate_limit_requests_per_minute: int = Field(600, env="ASTRODASH_RATE_LIMIT_REQUESTS_PER_MINUTE")
    rate_limit_burst_limit: int = Field(100, env="ASTRODASH_RATE_LIMIT_BURST_LIMIT")

    # Security Headers
    enable_hsts: bool = Field(True, env="ASTRODASH_ENABLE_HSTS")
    enable_csp: bool = Field(True, env="ASTRODASH_ENABLE_CSP")
    enable_permissions_policy: bool = Field(True, env="ASTRODASH_ENABLE_PERMISSIONS_POLICY")

    # Input Validation
    max_request_size: int = Field(100 * 1024 * 1024, env="ASTRODASH_MAX_REQUEST_SIZE")  # 100MB
    max_file_size: int = Field(50 * 1024 * 1024, env="ASTRODASH_MAX_FILE_SIZE")  # 50MB

    # Session Security
    session_cookie_secure: bool = Field(True, env="ASTRODASH_SESSION_COOKIE_SECURE")
    session_cookie_httponly: bool = Field(True, env="ASTRODASH_SESSION_COOKIE_HTTPONLY")
    session_cookie_samesite: str = Field("strict", env="ASTRODASH_SESSION_COOKIE_SAMESITE")

    # Database
    db_url: Optional[AnyUrl] = Field(None, env="ASTRODASH_DATABASE_URL")
    db_echo: bool = Field(False, env="ASTRODASH_DB_ECHO")

    # S3 Object Storage
    s3_endpoint_url: str = Field("", env="ASTRODASH_S3_ENDPOINT_URL")
    s3_access_key_id: str = Field("", env="ASTRODASH_S3_ACCESS_KEY_ID")
    s3_secret_access_key: str = Field("", env="ASTRODASH_S3_SECRET_ACCESS_KEY")
    s3_region_name: str = Field("", env="ASTRODASH_S3_REGION_NAME")
    s3_bucket: str = Field("", env="ASTRODASH_S3_BUCKET")

    # Data Storage (External to application code)
    data_dir: str = Field("/mnt/astrodash-data", env="ASTRODASH_DATA_DIR")
    storage_dir: str = Field("/mnt/astrodash-data", env="ASTRODASH_STORAGE_DIR")

    # ML Model Paths (External data directory)
    user_model_dir: str = Field("/mnt/astrodash-data/user_models", env="ASTRODASH_USER_MODEL_DIR")
    dash_model_path: str = Field("/mnt/astrodash-data/pre_trained_models/dash/zeroZ/pytorch_model.pth",
                                 env="ASTRODASH_DASH_MODEL_PATH")
    dash_training_params_path: str = Field("/mnt/astrodash-data/pre_trained_models/dash/zeroZ/training_params.pickle",
                                           env="ASTRODASH_DASH_TRAINING_PARAMS_PATH")
    transformer_model_path: str = Field("/mnt/astrodash-data/pre_trained_models/transformer/TF_wiserep_v6.pt",
                                        env="ASTRODASH_TRANSFORMER_MODEL_PATH")

    # website_final 1D CNN / latent (local astrodash-web paths for host runserver + compose binds)
    oned_cnn_z_model_path: str = Field(
        "/Users/jesuscaraball0/code/personal_code/astrodash-web/data/pre_trained_models/website_final/dash_z/model.pth",
        env="ASTRODASH_1DCNN_Z_MODEL_PATH",
    )
    oned_cnn_z_class_mapping_path: str = Field(
        "/Users/jesuscaraball0/code/personal_code/astrodash-web/data/pre_trained_models/website_final/dash_z/class_mapping.json",
        env="ASTRODASH_1DCNN_Z_CLASS_MAPPING_PATH",
    )
    oned_cnn_z_training_config_path: str = Field(
        "/Users/jesuscaraball0/code/personal_code/astrodash-web/data/pre_trained_models/website_final/dash_z/training_config.json",
        env="ASTRODASH_1DCNN_Z_TRAINING_CONFIG_PATH",
    )
    oned_cnn_noz_model_path: str = Field(
        "/Users/jesuscaraball0/code/personal_code/astrodash-web/data/pre_trained_models/website_final/dash_noz/model.pth",
        env="ASTRODASH_1DCNN_NOZ_MODEL_PATH",
    )
    oned_cnn_noz_class_mapping_path: str = Field(
        "/Users/jesuscaraball0/code/personal_code/astrodash-web/data/pre_trained_models/website_final/dash_noz/class_mapping.json",
        env="ASTRODASH_1DCNN_NOZ_CLASS_MAPPING_PATH",
    )
    oned_cnn_noz_training_config_path: str = Field(
        "/Users/jesuscaraball0/code/personal_code/astrodash-web/data/pre_trained_models/website_final/dash_noz/training_config.json",
        env="ASTRODASH_1DCNN_NOZ_TRAINING_CONFIG_PATH",
    )
    latent_z_encoder_path: str = Field(
        "/Users/jesuscaraball0/code/personal_code/astrodash-web/data/wiserep_henna/try_5/Dered36_5/best_ckpt.pt",
        env="ASTRODASH_LATENT_Z_ENCODER_PATH",
    )
    latent_z_encoder_cfg_path: str = Field(
        "/Users/jesuscaraball0/code/personal_code/astrodash-web/data/wiserep_henna/try_5/Dered36_5/cfg_used.json",
        env="ASTRODASH_LATENT_Z_ENCODER_CFG_PATH",
    )
    latent_z_classifier_path: str = Field(
        "/Users/jesuscaraball0/code/personal_code/astrodash-web/data/pre_trained_models/website_final/latent_z/classifier_best.pt",
        env="ASTRODASH_LATENT_Z_CLASSIFIER_PATH",
    )
    latent_z_classifier_cfg_path: str = Field(
        "/Users/jesuscaraball0/code/personal_code/astrodash-web/data/pre_trained_models/website_final/latent_z/cfg_used.json",
        env="ASTRODASH_LATENT_Z_CLASSIFIER_CFG_PATH",
    )
    latent_noz_encoder_path: str = Field(
        "/Users/jesuscaraball0/code/personal_code/astrodash-web/data/wiserep_henna/try_5_noz/Nodered36_5/best_ckpt.pt",
        env="ASTRODASH_LATENT_NOZ_ENCODER_PATH",
    )
    latent_noz_encoder_cfg_path: str = Field(
        "/Users/jesuscaraball0/code/personal_code/astrodash-web/data/wiserep_henna/try_5_noz/Nodered36_5/cfg_used.json",
        env="ASTRODASH_LATENT_NOZ_ENCODER_CFG_PATH",
    )
    latent_noz_classifier_path: str = Field(
        "/Users/jesuscaraball0/code/personal_code/astrodash-web/data/pre_trained_models/website_final/latent_noz/classifier_best.pt",
        env="ASTRODASH_LATENT_NOZ_CLASSIFIER_PATH",
    )
    latent_noz_classifier_cfg_path: str = Field(
        "/Users/jesuscaraball0/code/personal_code/astrodash-web/data/pre_trained_models/website_final/latent_noz/cfg_used.json",
        env="ASTRODASH_LATENT_NOZ_CLASSIFIER_CFG_PATH",
    )

    # Template and Line List Paths (External data directory)
    # Resolved in model_validator when default path is missing (e.g. dev without /mnt/astrodash-data)
    template_path: str = Field("/mnt/astrodash-data/pre_trained_models/templates/sn_and_host_templates.npz",
                               env="ASTRODASH_TEMPLATE_PATH")
    line_list_path: str = Field("/mnt/astrodash-data/pre_trained_models/templates/sneLineList.txt",
                                env="ASTRODASH_LINE_LIST_PATH")

    # ML Configuration Parameters
    # DASH model parameters
    nw: int = Field(1024, env="ASTRODASH_NW")  # Number of wavelength bins
    w0: float = Field(3500.0, env="ASTRODASH_W0")  # Minimum wavelength in Angstroms
    w1: float = Field(10000.0, env="ASTRODASH_W1")  # Maximum wavelength in Angstroms

    # Transformer model parameters
    label_mapping: Dict[str, int] = Field(
        {'Ia': 0, 'IIn': 1, 'SLSNe-I': 2, 'II': 3, 'Ib/c': 4},
        env="ASTRODASH_LABEL_MAPPING"
    )

    # Transformer architecture parameters
    transformer_bottleneck_length: int = Field(1, env="ASTRODASH_TRANSFORMER_BOTTLENECK_LENGTH")
    transformer_model_dim: int = Field(128, env="ASTRODASH_TRANSFORMER_MODEL_DIM")
    transformer_num_heads: int = Field(4, env="ASTRODASH_TRANSFORMER_NUM_HEADS")
    transformer_num_layers: int = Field(6, env="ASTRODASH_TRANSFORMER_NUM_LAYERS")
    transformer_ff_dim: int = Field(256, env="ASTRODASH_TRANSFORMER_FF_DIM")
    transformer_dropout: float = Field(0.1, env="ASTRODASH_TRANSFORMER_DROPOUT")
    transformer_selfattn: bool = Field(False, env="ASTRODASH_TRANSFORMER_SELFATTN")

    # 1D CNN / latent — architecture frozen with the checkpoints
    website_final_label_mapping: Dict[str, int] = Field(
        {"SN Ia": 0, "SN Ib/c": 1, "SN II": 2, "SN IIn": 3, "SLSN-I": 4},
        env="ASTRODASH_WEBSITE_FINAL_LABEL_MAPPING",
    )
    latent_encoder_n_wave: int = Field(1320, env="ASTRODASH_LATENT_ENCODER_N_WAVE")
    latent_encoder_lam_min: float = Field(3200.0, env="ASTRODASH_LATENT_ENCODER_LAM_MIN")
    latent_encoder_lam_max: float = Field(9800.0, env="ASTRODASH_LATENT_ENCODER_LAM_MAX")
    latent_encoder_dlam: float = Field(5.0, env="ASTRODASH_LATENT_ENCODER_DLAM")
    latent_encoder_min_finite_bins: int = Field(
        50, env="ASTRODASH_LATENT_ENCODER_MIN_FINITE_BINS"
    )
    latent_encoder_flux_clip: float = Field(50.0, env="ASTRODASH_LATENT_ENCODER_FLUX_CLIP")
    latent_encoder_bottleneck_length: int = Field(
        16, env="ASTRODASH_LATENT_ENCODER_BOTTLENECK_LENGTH"
    )
    latent_encoder_bottleneck_dim: int = Field(
        64, env="ASTRODASH_LATENT_ENCODER_BOTTLENECK_DIM"
    )
    latent_encoder_model_dim: int = Field(128, env="ASTRODASH_LATENT_ENCODER_MODEL_DIM")
    latent_encoder_num_heads: int = Field(4, env="ASTRODASH_LATENT_ENCODER_NUM_HEADS")
    latent_encoder_num_layers: int = Field(4, env="ASTRODASH_LATENT_ENCODER_NUM_LAYERS")
    latent_encoder_ff_dim: int = Field(256, env="ASTRODASH_LATENT_ENCODER_FF_DIM")
    latent_encoder_dropout: float = Field(0.15, env="ASTRODASH_LATENT_ENCODER_DROPOUT")
    latent_encoder_selfattn: bool = Field(False, env="ASTRODASH_LATENT_ENCODER_SELFATTN")
    latent_encoder_concat: bool = Field(True, env="ASTRODASH_LATENT_ENCODER_CONCAT")
    latent_encoder_cross_attn_only: bool = Field(
        False, env="ASTRODASH_LATENT_ENCODER_CROSS_ATTN_ONLY"
    )
    latent_encoder_hidden_len: int = Field(256, env="ASTRODASH_LATENT_ENCODER_HIDDEN_LEN")
    latent_mlp_head_hidden: int = Field(256, env="ASTRODASH_LATENT_MLP_HEAD_HIDDEN")
    latent_mlp_head_dropout: float = Field(0.25, env="ASTRODASH_LATENT_MLP_HEAD_DROPOUT")

    def oned_cnn_input_length(self) -> int:
        return self.nw + 1

    def website_final_idx_to_label(self) -> Dict[int, str]:
        return {idx: name for name, idx in self.website_final_label_mapping.items()}

    def website_final_class_names(self) -> List[str]:
        return [
            name
            for name, _ in sorted(
                self.website_final_label_mapping.items(), key=lambda item: item[1]
            )
        ]

    def latent_encoder_latent_flat(self) -> int:
        return self.latent_encoder_bottleneck_length * self.latent_encoder_bottleneck_dim

    def latent_encoder_ctor_kwargs(self) -> Dict[str, Any]:
        return {
            "bottleneck_length": self.latent_encoder_bottleneck_length,
            "bottleneck_dim": self.latent_encoder_bottleneck_dim,
            "model_dim": self.latent_encoder_model_dim,
            "num_heads": self.latent_encoder_num_heads,
            "num_layers": self.latent_encoder_num_layers,
            "ff_dim": self.latent_encoder_ff_dim,
            "dropout": self.latent_encoder_dropout,
            "selfattn": self.latent_encoder_selfattn,
            "concat": self.latent_encoder_concat,
            "cross_attn_only": self.latent_encoder_cross_attn_only,
            "hidden_len": self.latent_encoder_hidden_len,
        }

    def latent_encoder_wavelength_grid(self) -> List[float]:
        nbins = int(
            (self.latent_encoder_lam_max - self.latent_encoder_lam_min)
            / self.latent_encoder_dlam
        )
        edges = [
            self.latent_encoder_lam_min + self.latent_encoder_dlam * i
            for i in range(nbins + 1)
        ]
        return [0.5 * (edges[i] + edges[i + 1]) for i in range(nbins)]

    # User model parameters
    user_model_reliability_threshold: float = Field(0.5, env="ASTRODASH_USER_MODEL_RELIABILITY_THRESHOLD")

    # Logging
    log_dir: str = Field("logs", env="ASTRODASH_LOG_DIR")
    log_level: str = Field("INFO", env="ASTRODASH_LOG_LEVEL")

    # Other
    osc_api_url: str = Field("https://api.astrocats.space", env="ASTRODASH_OSC_API_URL")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "allow"  # Allow extra fields from environment

    @field_validator("allowed_hosts", "cors_origins", mode="before")
    @classmethod
    def split_str(cls, v):
        if isinstance(v, str):
            return [i.strip() for i in v.split(",") if i.strip()]
        return v

    @field_validator("label_mapping", "website_final_label_mapping", mode="before")
    @classmethod
    def parse_label_mapping(cls, v):
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return v
        return v

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, v):
        if v == "supersecret" and os.getenv("ENVIRONMENT") == "production":
            raise ValueError("SECRET_KEY must be set to a secure value in production")
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters long")
        return v

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v):
        allowed_environments = ["development", "staging", "production", "test"]
        if v not in allowed_environments:
            raise ValueError(f"Environment must be one of: {allowed_environments}")
        return v

    @field_validator("session_cookie_samesite")
    @classmethod
    def validate_session_cookie_samesite(cls, v):
        allowed_values = ["strict", "lax", "none"]
        if v not in allowed_values:
            raise ValueError(f"SESSION_COOKIE_SAMESITE must be one of: {allowed_values}")
        return v

    @model_validator(mode="after")
    def resolve_data_paths_when_missing(self):
        """When line_list_path or template_path does not exist, use the same relative path
        under data_dir (pre_trained_models/templates/). Set ASTRODASH_DATA_DIR so the file
        is found there."""
        templates_subdir = os.path.join("pre_trained_models", "templates")
        if not os.path.exists(self.line_list_path):
            candidate = os.path.join(self.data_dir, templates_subdir, "sneLineList.txt")
            if os.path.exists(candidate):
                object.__setattr__(self, "line_list_path", candidate)
        if not os.path.exists(self.template_path):
            candidate = os.path.join(self.data_dir, templates_subdir, "sn_and_host_templates.npz")
            if os.path.exists(candidate):
                object.__setattr__(self, "template_path", candidate)
        return self


def get_settings() -> Settings:
    return Settings()
