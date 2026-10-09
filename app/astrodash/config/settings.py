from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import DotEnvSettingsSource, EnvSettingsSource
from pydantic import AliasChoices, Field, AnyUrl, field_validator, model_validator
from typing import Any, Optional, List, Dict
import os


# Field groups shared by the raw-string sources below and the mode="before"
# validators further down, so the two lists cannot drift apart.
_HOST_LIST_FIELDS = ("allowed_hosts", "cors_origins")
_LABEL_MAPPING_FIELDS = ("label_mapping", "website_final_label_mapping")
_RAW_STRING_FIELDS = frozenset(_HOST_LIST_FIELDS + _LABEL_MAPPING_FIELDS)


class _RawStringDecodeMixin:
    """Let the string-parsing fields see their raw configured value.

    pydantic-settings JSON-decodes every complex-typed field before any
    validator runs, so ``ASTRODASH_ALLOWED_HOSTS="a.example,b.example"``
    raises ``SettingsError`` instead of reaching ``split_str``. The fields in
    ``_RAW_STRING_FIELDS`` each carry a ``mode="before"`` validator that
    already accepts the string form, so decode as JSON where that succeeds and
    hand the raw string over where it does not. Both spellings keep working:
    ``["a.example"]`` and ``a.example,b.example``.

    Mixed into both sources on purpose. Wrapping only the process-environment
    source would leave the same value failing when it came from an env file.
    """

    def decode_complex_value(self, field_name, field, value):
        if field_name in _RAW_STRING_FIELDS:
            try:
                return super().decode_complex_value(field_name, field, value)
            except ValueError:
                return value
        return super().decode_complex_value(field_name, field, value)


class _AstrodashEnvSettingsSource(_RawStringDecodeMixin, EnvSettingsSource):
    pass


class _AstrodashDotEnvSettingsSource(_RawStringDecodeMixin, DotEnvSettingsSource):
    pass


class Settings(BaseSettings):
    # General
    app_name: str = Field("AstroDash API")
    environment: str = Field("production")
    debug: bool = Field(False)

    # API
    api_prefix: str = Field("/api/v1")
    allowed_hosts: List[str] = Field(["*"])  # Allow all hosts for API usage
    cors_origins: List[str] = Field(["*"])    # Allow all origins for API usage

    # Security Settings
    secret_key: str = Field("your-super-secret-key-here-make-it-very-long-and-secure-32-chars-min")
    access_token_expire_minutes: int = Field(60 * 24)

    # Rate Limiting
    rate_limit_requests_per_minute: int = Field(600)
    rate_limit_burst_limit: int = Field(100)

    # Security Headers
    enable_hsts: bool = Field(True)
    enable_csp: bool = Field(True)
    enable_permissions_policy: bool = Field(True)

    # Input Validation
    max_request_size: int = Field(100 * 1024 * 1024)  # 100MB
    max_file_size: int = Field(50 * 1024 * 1024)  # 50MB

    # Session Security
    session_cookie_secure: bool = Field(True)
    session_cookie_httponly: bool = Field(True)
    session_cookie_samesite: str = Field("strict")

    # Database
    db_url: Optional[AnyUrl] = Field(
        None,
        validation_alias=AliasChoices("ASTRODASH_DATABASE_URL"),
    )
    db_echo: bool = Field(False)

    # S3 Object Storage
    s3_endpoint_url: str = Field("")
    s3_access_key_id: str = Field("")
    s3_secret_access_key: str = Field("")
    s3_region_name: str = Field("")
    s3_bucket: str = Field("")

    # Data Storage (External to application code)
    data_dir: str = Field("/mnt/astrodash-data")
    storage_dir: str = Field("/mnt/astrodash-data")

    # ML Model Paths (External data directory)
    user_model_dir: str = Field("/mnt/astrodash-data/user_models")
    dash_model_path: str = Field("/mnt/astrodash-data/pre_trained_models/dash/zeroZ/pytorch_model.pth")
    dash_training_params_path: str = Field("/mnt/astrodash-data/pre_trained_models/dash/zeroZ/training_params.pickle")
    transformer_model_path: str = Field("/mnt/astrodash-data/pre_trained_models/transformer/TF_wiserep_v6.pt")

    # website_final 1D CNN / latent (External data directory)
    # Layout under data_dir mirrors the dash/transformer entries above; the
    # artifacts ship in the S3 data manifest (docs/admin/updating-data-files.md).
    oned_cnn_z_model_path: str = Field(
        "/mnt/astrodash-data/pre_trained_models/1dcnn/z/model.pth",
        validation_alias=AliasChoices("ASTRODASH_1DCNN_Z_MODEL_PATH"),
    )
    oned_cnn_z_class_mapping_path: str = Field(
        "/mnt/astrodash-data/pre_trained_models/1dcnn/z/class_mapping.json",
        validation_alias=AliasChoices("ASTRODASH_1DCNN_Z_CLASS_MAPPING_PATH"),
    )
    oned_cnn_noz_model_path: str = Field(
        "/mnt/astrodash-data/pre_trained_models/1dcnn/noz/model.pth",
        validation_alias=AliasChoices("ASTRODASH_1DCNN_NOZ_MODEL_PATH"),
    )
    oned_cnn_noz_class_mapping_path: str = Field(
        "/mnt/astrodash-data/pre_trained_models/1dcnn/noz/class_mapping.json",
        validation_alias=AliasChoices("ASTRODASH_1DCNN_NOZ_CLASS_MAPPING_PATH"),
    )
    latent_z_encoder_path: str = Field("/mnt/astrodash-data/pre_trained_models/latent/z/encoder.pt")
    latent_z_classifier_path: str = Field("/mnt/astrodash-data/pre_trained_models/latent/z/classifier.pt")
    latent_noz_encoder_path: str = Field("/mnt/astrodash-data/pre_trained_models/latent/noz/encoder.pt")
    latent_noz_classifier_path: str = Field("/mnt/astrodash-data/pre_trained_models/latent/noz/classifier.pt")

    # Template and Line List Paths (External data directory)
    # Resolved in model_validator when default path is missing (e.g. dev without /mnt/astrodash-data)
    template_path: str = Field("/mnt/astrodash-data/pre_trained_models/templates/sn_and_host_templates.npz")
    line_list_path: str = Field("/mnt/astrodash-data/pre_trained_models/templates/sneLineList.txt")

    # ML Configuration Parameters
    # DASH model parameters
    nw: int = Field(1024)  # Number of wavelength bins
    w0: float = Field(3500.0)  # Minimum wavelength in Angstroms
    w1: float = Field(10000.0)  # Maximum wavelength in Angstroms

    # Transformer model parameters
    label_mapping: Dict[str, int] = Field({'Ia': 0, 'IIn': 1, 'SLSNe-I': 2, 'II': 3, 'Ib/c': 4})

    # Transformer architecture parameters
    transformer_bottleneck_length: int = Field(1)
    transformer_model_dim: int = Field(128)
    transformer_num_heads: int = Field(4)
    transformer_num_layers: int = Field(6)
    transformer_ff_dim: int = Field(256)
    transformer_dropout: float = Field(0.1)
    transformer_selfattn: bool = Field(False)

    # 1D CNN / latent — architecture frozen with the checkpoints
    website_final_label_mapping: Dict[str, int] = Field({"SN Ia": 0, "SN Ib/c": 1, "SN II": 2, "SN IIn": 3, "SLSN-I": 4})
    latent_encoder_n_wave: int = Field(1320)
    latent_encoder_lam_min: float = Field(3200.0)
    latent_encoder_lam_max: float = Field(9800.0)
    latent_encoder_dlam: float = Field(5.0)
    latent_encoder_min_finite_bins: int = Field(50)
    latent_encoder_flux_clip: float = Field(50.0)
    latent_encoder_bottleneck_length: int = Field(16)
    latent_encoder_bottleneck_dim: int = Field(64)
    latent_encoder_model_dim: int = Field(128)
    latent_encoder_num_heads: int = Field(4)
    latent_encoder_num_layers: int = Field(4)
    latent_encoder_ff_dim: int = Field(256)
    latent_encoder_dropout: float = Field(0.15)
    latent_encoder_selfattn: bool = Field(False)
    latent_encoder_concat: bool = Field(True)
    latent_encoder_cross_attn_only: bool = Field(False)
    latent_encoder_hidden_len: int = Field(256)
    latent_mlp_head_hidden: int = Field(256)
    latent_mlp_head_dropout: float = Field(0.25)

    def oned_cnn_input_length(self) -> int:
        return self.nw + 1

    def website_final_idx_to_label(self) -> Dict[int, str]:
        return {idx: name for name, idx in self.website_final_label_mapping.items()}

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
    user_model_reliability_threshold: float = Field(0.5)

    # WISeREP transport
    # Bounds on the by-name lookup's outbound requests. The export is capped
    # at 50 rows per page and takes a measured 5.4 to 10.2 seconds per page,
    # against a 300-second deployment limit across two workers; the deadline
    # and page cap below keep a slow upstream from holding a worker past it.
    # The request interval matches the spacing the offline scraper applies.
    wiserep_base_url: str = "https://www.wiserep.org"
    wiserep_connect_timeout: float = 5.0
    wiserep_read_timeout: float = 30.0
    wiserep_total_deadline_seconds: float = 120.0
    wiserep_page_cap: int = 10
    wiserep_min_request_interval_seconds: float = 1.0
    wiserep_download_deadline_seconds: float = 60.0

    # Logging
    log_dir: str = Field("logs")
    log_level: str = Field("INFO")

    # Other
    osc_api_url: str = Field("https://api.astrocats.space")

    # env_prefix is what makes the documented ASTRODASH_* names bind. Without
    # it pydantic-settings looks up the bare field name, so every ASTRODASH_*
    # variable is read as unset. case_sensitive must stay False so the
    # upper-case variable reaches the lower-case field. Five fields whose
    # documented name the prefix cannot produce carry a validation_alias.
    model_config = SettingsConfigDict(
        env_prefix="ASTRODASH_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # The five aliased fields below are otherwise unsettable by keyword:
        # Settings(db_url=...) would silently land in model_extra.
        populate_by_name=True,
        # An empty ASTRODASH_* value means "unset", not "blank this default".
        # env/.env.default already uses blank placeholders (ASTRODASH_S3_REGION_NAME =).
        env_ignore_empty=True,
        extra="allow",  # Allow extra fields from environment
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """Swap in the env source that preserves raw strings."""
        return (
            init_settings,
            _AstrodashEnvSettingsSource(settings_cls),
            _AstrodashDotEnvSettingsSource(settings_cls),
            file_secret_settings,
        )

    @field_validator(*_HOST_LIST_FIELDS, mode="before")
    @classmethod
    def split_str(cls, v):
        if isinstance(v, str):
            return [i.strip() for i in v.split(",") if i.strip()]
        return v

    @field_validator(*_LABEL_MAPPING_FIELDS, mode="before")
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
