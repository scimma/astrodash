import numpy as np
from scipy.signal import medfilt
from scipy.interpolate import UnivariateSpline
from typing import Dict, Tuple, Optional, Union
from astrodash.config.logging import get_logger
from astrodash.config.settings import get_settings
from astrodash.shared.utils.helpers import sort_wave_flux
from astrodash.shared.utils.validators import validate_spectrum, ValidationError

logger = get_logger(__name__)

class DashSpectrumProcessor:
    """
    Handles all preprocessing for the Dash (CNN) classifier.
    Includes normalization, wavelength binning, continuum removal, mean zeroing, and apodization.
    """

    # Configuration constants
    DEFAULT_EDGE_WIDTH = 50
    DEFAULT_EDGE_RATIO = 4
    DEFAULT_OUTER_VAL = 0.5
    MIN_FILTER_SIZE = 3

    def __init__(self, w0: float, w1: float, nw: int, num_spline_points: int = 13):
        """
        Initialize the DashSpectrumProcessor.

        Args:
            w0: Minimum wavelength in Angstroms
            w1: Maximum wavelength in Angstroms
            nw: Number of wavelength bins
            num_spline_points: Number of points for spline fitting

        Raises:
            ValueError: If parameters are invalid
        """
        if w0 <= 0 or w1 <= 0 or w0 >= w1:
            raise ValueError(f"Invalid wavelength range: w0={w0}, w1={w1}")
        if nw <= 0:
            raise ValueError(f"Invalid number of bins: nw={nw}")
        if num_spline_points < 3:
            raise ValueError(f"Invalid spline points: {num_spline_points} (minimum 3)")

        self.w0 = float(w0)
        self.w1 = float(w1)
        self.nw = int(nw)
        self.num_spline_points = int(num_spline_points)

        logger.info(f"DashSpectrumProcessor initialized: w0={w0}, w1={w1}, nw={nw}")

    def process(
        self,
        wave: np.ndarray,
        flux: np.ndarray,
        z: float,
        smooth: int = 0,
        min_wave: Optional[float] = None,
        max_wave: Optional[float] = None
    ) -> Tuple[np.ndarray, int, int, float]:
        """
        Full preprocessing pipeline for Dash classifier.

        Args:
            wave: Wavelength array in Angstroms
            flux: Flux array (arbitrary units)
            z: Redshift value
            smooth: Smoothing factor (0 = no smoothing)
            min_wave: Minimum wavelength cutoff
            max_wave: Maximum wavelength cutoff

        Returns:
            Tuple of (processed_flux, min_idx, max_idx, z)

        Raises:
            ValidationError: If processing fails or spectrum is out of range
        """
        try:
            validate_spectrum(wave.tolist(), flux.tolist(), z)

            # 1) Initial normalisation and wavelength limiting
            flux_norm = self.normalise_spectrum(flux)

            effective_min = self.w0 if min_wave is None else min_wave
            effective_max = self.w1 if max_wave is None else max_wave
            flux_limited = self.limit_wavelength_range(wave, flux_norm, effective_min, effective_max)

            # 2) Smoothing with median filter (match original DASH kernel logic)
            effective_smooth = smooth if smooth > 0 else 6
            w_density = (self.w1 - self.w0) / self.nw
            wavelength_density = (np.max(wave) - np.min(wave)) / max(len(wave), 1)
            if wavelength_density <= 0:
                filter_size = 1
            else:
                filter_size = int(w_density / wavelength_density * effective_smooth / 2) * 2 + 1
            if filter_size < self.MIN_FILTER_SIZE:
                filter_size = self.MIN_FILTER_SIZE
            if filter_size % 2 == 0:
                filter_size += 1
            
            # Cap to array length to avoid "kernel_size exceeds volume extent" and very slow medfilt
            n_flux = len(flux_limited)
            if filter_size > n_flux:
                filter_size = n_flux if n_flux % 2 == 1 else max(1, n_flux - 1)

            flux_smoothed = medfilt(flux_limited, kernel_size=filter_size)

            # 3) Derive redshifted spectrum, restrict to model range, re-normalise
            wave_deredshifted = wave / (1 + z)
            if len(wave_deredshifted) < 2:
                raise ValidationError("Spectrum is out of classification range after deredshifting")

            mask = (wave_deredshifted >= self.w0) & (wave_deredshifted < self.w1)
            wave_dereds = wave_deredshifted[mask]
            flux_dereds = flux_smoothed[mask]
            if wave_dereds.size == 0:
                raise ValidationError(
                    f"Spectrum out of wavelength range [{self.w0}, {self.w1}] after deredshifting"
                )
            flux_dereds = self.normalise_spectrum(flux_dereds)

            # 4) Log-wavelength binning
            binned_wave, binned_flux, min_idx, max_idx = self.log_wavelength_binning(
                wave_dereds, flux_dereds
            )

            # Guard against completely empty or pathological spectra
            if min_idx == max_idx == 0 and not np.any(binned_flux):
                flat = np.full(self.nw, self.DEFAULT_OUTER_VAL, dtype=float)
                return flat, 0, 0, z

            # 5) Continuum removal (match DASH semantics)
            cont_removed, _ = self.continuum_removal(binned_wave, binned_flux, min_idx, max_idx)

            # 6) Mean zero within valid region
            mean_zero_flux = self.mean_zero(cont_removed, min_idx, max_idx)

            # 7) Apodize (cosine bell) without outer offset
            apodized_flux = self.apodize(mean_zero_flux, min_idx, max_idx)

            # 8) Final normalisation and zero_non_overlap_part with outerVal=0.5
            flux_norm_final = self.normalise_spectrum(apodized_flux)
            flux_norm_final = self.zero_non_overlap_part(
                flux_norm_final, min_idx, max_idx, self.DEFAULT_OUTER_VAL
            )

            logger.debug(f"Processing completed: min_idx={min_idx}, max_idx={max_idx}")
            return flux_norm_final, min_idx, max_idx, z

        except ValidationError:
            # Re-raise ValidationError as-is
            raise
        except Exception as e:
            logger.error(f"Spectrum processing failed: {str(e)}")
            raise ValidationError(f"Spectrum processing failed: {str(e)}") from e

    def _apply_smoothing(self, wave: np.ndarray, flux: np.ndarray, smooth: int) -> np.ndarray:
        """Apply median filtering for smoothing."""
        try:
            wavelength_density = (np.max(wave) - np.min(wave)) / len(wave)
            w_density = (self.w1 - self.w0) / self.nw
            filter_size = int(w_density / wavelength_density * smooth / 2) * 2 + 1

            if filter_size >= self.MIN_FILTER_SIZE:
                flux_smoothed = medfilt(flux, kernel_size=filter_size)
                logger.debug(f"Applied smoothing with filter size {filter_size}")
                return flux_smoothed
            else:
                logger.warning(f"Filter size {filter_size} too small, skipping smoothing")
                return flux
        except Exception as e:
            logger.warning(f"Smoothing failed: {str(e)}, returning original flux")
            return flux

    @staticmethod
    def normalise_spectrum(flux: np.ndarray) -> np.ndarray:
        """
        Normalize flux array to [0, 1] range.

        Args:
            flux: Input flux array

        Returns:
            Normalized flux array

        Raises:
            ValidationError: If normalization fails
        """
        if len(flux) == 0:
            raise ValidationError("Cannot normalize empty array")

        flux_min, flux_max = np.min(flux), np.max(flux)

        if not np.isfinite(flux_min) or not np.isfinite(flux_max):
            raise ValidationError("Array contains non-finite values")

        if flux_min == flux_max:
            logger.warning("Normalizing spectrum: constant flux array")
            return np.zeros(len(flux))

        # Avoid division by zero
        if flux_max <= flux_min:
            raise ValidationError(f"Invalid flux range: min={flux_min}, max={flux_max}")

        return (flux - flux_min) / (flux_max - flux_min)

    @staticmethod
    def limit_wavelength_range(
        wave: np.ndarray,
        flux: np.ndarray,
        min_wave: Optional[float],
        max_wave: Optional[float]
    ) -> np.ndarray:
        """
        Limit flux values outside specified wavelength range.

        Args:
            wave: Wavelength array
            flux: Flux array
            min_wave: Minimum wavelength cutoff
            max_wave: Maximum wavelength cutoff

        Returns:
            Modified flux array
        """
        flux_out = np.copy(flux)

        if min_wave is not None and np.isfinite(min_wave):
            min_idx = int((np.abs(np.asarray(wave) - min_wave)).argmin())
            flux_out[:min_idx] = 0

        if max_wave is not None and np.isfinite(max_wave):
            max_idx = int((np.abs(np.asarray(wave) - max_wave)).argmin())
            flux_out[max_idx:] = 0

        return flux_out

    def log_wavelength_binning(self, wave: np.ndarray, flux: np.ndarray) -> Tuple[np.ndarray, np.ndarray, int, int]:
        """
        Bin flux to log-wavelength grid.

        Args:
            wave: Input wavelength array
            flux: Input flux array

        Returns:
            Tuple of (binned_wavelength, binned_flux, min_index, max_index)
        """
        try:
            dwlog = np.log(self.w1 / self.w0) / self.nw
            wlog = self.w0 * np.exp(np.arange(0, self.nw) * dwlog)
            binned_flux = np.interp(wlog, wave, flux, left=0, right=0)

            # Find non-zero region
            non_zero_indices = np.where(binned_flux != 0)[0]

            if len(non_zero_indices) == 0:
                min_index = max_index = 0
            else:
                min_index = non_zero_indices[0]
                max_index = non_zero_indices[-1]

            return wlog, binned_flux, min_index, max_index

        except Exception as e:
            logger.error(f"Wavelength binning failed: {str(e)}")
            raise ValidationError(f"Wavelength binning failed: {str(e)}") from e

    def continuum_removal(self, wave: np.ndarray, flux: np.ndarray, min_idx: int, max_idx: int) -> Tuple[np.ndarray, np.ndarray]:
        """
        Remove continuum from spectrum using spline fitting.

        Args:
            wave: Wavelength array
            flux: Flux array
            min_idx: Start index of valid region
            max_idx: End index of valid region

        Returns:
            Tuple of (continuum_subtracted_flux, continuum)
        """
        try:
            # Validate indices
            min_idx = int(np.clip(min_idx, 0, len(flux) - 1))
            max_idx = int(np.clip(max_idx, min_idx, len(flux) - 1))

            wave_region = wave[min_idx:max_idx + 1]
            flux_region = flux[min_idx:max_idx + 1]

            # Match DASH semantics: shift flux by +1, divide by spline continuum, then normalise (flux-1)
            flux_plus = flux + 1.0
            cont_removed = np.copy(flux_plus)

            continuum = np.zeros_like(flux_plus)
            if len(wave_region) > self.num_spline_points and (max_idx - min_idx) > 5:
                spline = UnivariateSpline(
                    wave[min_idx:max_idx + 1], flux_plus[min_idx:max_idx + 1], k=3
                )
                spline_wave = np.linspace(wave[min_idx], wave[max_idx],
                                          num=self.num_spline_points, endpoint=True)
                spline_points = spline(spline_wave)
                spline_more = UnivariateSpline(spline_wave, spline_points, k=3)
                spline_points_more = spline_more(wave[min_idx:max_idx + 1])
                continuum[min_idx:max_idx + 1] = spline_points_more
            else:
                continuum[min_idx:max_idx + 1] = 1.0

            valid = continuum[min_idx:max_idx + 1] != 0
            if np.any(valid):
                cont_removed[min_idx:max_idx + 1][valid] = (
                    flux_plus[min_idx:max_idx + 1][valid] / continuum[min_idx:max_idx + 1][valid]
                )

            cont_removed_norm = DashSpectrumProcessor.normalise_spectrum(cont_removed - 1.0)
            cont_removed_norm[:min_idx] = 0.0
            cont_removed_norm[max_idx + 1:] = 0.0

            return cont_removed_norm, continuum - 1.0

        except Exception as e:
            logger.error(f"Continuum removal failed: {str(e)}")
            raise ValidationError(f"Continuum removal failed: {str(e)}") from e

    @staticmethod
    def mean_zero(flux: np.ndarray, min_idx: int, max_idx: int) -> np.ndarray:
        """
        Zero-mean the flux array within the specified region, matching
        original DASH behaviour:
        - subtract mean within [min_idx, max_idx)
        - keep outer regions equal to the original flux.
        """
        if flux.size == 0:
            return flux

        min_idx = int(np.clip(min_idx, 0, len(flux) - 1))
        max_idx = int(np.clip(max_idx, min_idx, len(flux) - 1))

        if max_idx <= min_idx:
            return flux

        out = np.copy(flux)
        mean_flux = np.mean(out[min_idx:max_idx])
        out[min_idx : max_idx + 1] = out[min_idx : max_idx + 1] - mean_flux
        
        # outer regions unchanged
        return out

    @staticmethod
    def apodize(flux: np.ndarray, min_idx: int, max_idx: int) -> np.ndarray:
        """
        Apply apodization to reduce edge effects using a 5% cosine bell,
        consistent with the original DASH implementation.
        """
        if flux.size == 0:
            return flux

        out = np.copy(flux)
        nw = len(out)
        min_idx = int(np.clip(min_idx, 0, nw - 1))
        max_idx = int(np.clip(max_idx, min_idx, nw - 1))

        percent = 0.05
        nsquash = int(nw * percent)
        if nsquash <= 1:
            return out

        for i in range(nsquash):
            arg = np.pi * i / (nsquash - 1)
            factor = 0.5 * (1.0 - np.cos(arg))
            if (min_idx + i < nw) and (max_idx - i >= 0):
                out[min_idx + i] = factor * out[min_idx + i]
                out[max_idx - i] = factor * out[max_idx - i]
            else:
                break

        return out

    @staticmethod
    def zero_non_overlap_part(
        array: np.ndarray,
        min_index: int,
        max_index: int,
        outer_val: float = 0.0
    ) -> np.ndarray:
        """
        Set regions outside the valid range to a specified value.
        Matches DASH behaviour: indices < min_index and > max_index
        are set to outer_val; the valid region [min_index, max_index]
        is preserved.
        """
        sliced_array = np.copy(array)

        # Validate indices
        min_index = np.clip(min_index, 0, len(sliced_array) - 1)
        max_index = np.clip(max_index, min_index, len(sliced_array) - 1)

        # Set outer regions
        sliced_array[:min_index] = outer_val
        sliced_array[max_index + 1:] = outer_val

        return sliced_array


class TransformerSpectrumProcessor:
    """
    Handles preprocessing for the Transformer classifier.
    Includes interpolation to target length and normalization.
    """

    def __init__(self, target_length: int = 1024):
        """
        Initialize the TransformerSpectrumProcessor.

        Args:
            target_length: Target length for interpolation

        Raises:
            ValueError: If target_length is invalid
        """
        if target_length <= 0:
            raise ValueError(f"Invalid target length: {target_length}")

        self.target_length = int(target_length)
        logger.info(f"TransformerSpectrumProcessor initialized with target length: {target_length}")

    def process(self, x: Union[np.ndarray, list], y: Union[np.ndarray, list], redshift: float = 0.0) -> Tuple[np.ndarray, np.ndarray, float]:
        """
        Interpolate and normalize spectrum data for transformer input.

        Args:
            x: Wavelength array
            y: Flux array
            redshift: Redshift value

        Returns:
            Tuple of (interpolated_x, normalized_y, redshift)

        Raises:
            ValidationError: If processing fails
        """
        try:
            validate_spectrum(x if isinstance(x, list) else x.tolist(),
                           y if isinstance(y, list) else y.tolist(),
                           redshift)

            # Convert to numpy arrays
            x_array = np.asarray(x, dtype=np.float64)
            y_array = np.asarray(y, dtype=np.float64)

            # Interpolate to target length
            x_interp = self._interpolate_to_length(x_array, self.target_length)
            y_interp = self._interpolate_to_length(y_array, self.target_length)

            # Normalize flux
            y_norm = self._normalize(y_interp)

            logger.debug(f"Transformer processing completed: input_length={len(x)}, output_length={self.target_length}")
            return x_interp, y_norm, redshift

        except ValidationError:
            # Re-raise ValidationError as-is
            raise
        except Exception as e:
            logger.error(f"Transformer processing failed: {str(e)}")
            raise ValidationError(f"Transformer processing failed: {str(e)}") from e

    def _interpolate_to_length(self, arr: np.ndarray, length: int) -> np.ndarray:
        """
        Interpolate array to target length.

        Args:
            arr: Input array
            length: Target length

        Returns:
            Interpolated array
        """
        if len(arr) == length:
            return arr

        # Create normalized coordinate systems
        x_old = np.linspace(0, 1, len(arr))
        x_new = np.linspace(0, 1, length)

        # Interpolate
        return np.interp(x_new, x_old, arr)

    def _normalize(self, arr: np.ndarray) -> np.ndarray:
        """
        Normalize array to [0, 1] range.

        Args:
            arr: Input array

        Returns:
            Normalized array

        Raises:
            ValidationError: If normalization fails
        """
        if len(arr) == 0:
            raise ValidationError("Cannot normalize empty array")

        arr_min, arr_max = np.min(arr), np.max(arr)

        if not np.isfinite(arr_min) or not np.isfinite(arr_max):
            raise ValidationError("Array contains non-finite values")

        if np.isclose(arr_min, arr_max):
            logger.warning("Normalizing transformer input: constant array")
            return np.zeros(len(arr))

        # Avoid division by zero
        if arr_max <= arr_min:
            raise ValidationError(f"Invalid array range: min={arr_min}, max={arr_max}")

        return (arr - arr_min) / (arr_max - arr_min)


class OnedCnnSpectrumProcessor:
    """website_final 1D CNN input: DASH log-bins + redshift (or 0.0) as last sample.

    Uses :class:`DashSpectrumProcessor` with the same ``Settings.w0`` / ``w1`` /
    ``nw`` as original DASH (the grid that guided 1D CNN training). For the
    z-variant, pass ``include_redshift=True``: the processor deredshifts with
    the true ``z`` then concatenates that ``z``. For no-z, pass
    ``include_redshift=False``: the processor is called with ``z=0`` (observed
    frame) and ``0.0`` is concatenated so the vector stays length ``nw + 1``.
    """

    def __init__(self, processor: Optional[DashSpectrumProcessor] = None):
        if processor is None:
            settings = get_settings()
            processor = DashSpectrumProcessor(
                w0=settings.w0,
                w1=settings.w1,
                nw=settings.nw,
            )
        self.processor = processor

    def process(
        self,
        wave: Union[np.ndarray, list],
        flux: Union[np.ndarray, list],
        redshift: float,
        include_redshift: bool,
    ) -> np.ndarray:
        wave_arr, flux_arr = sort_wave_flux(wave, flux)
        z_process = float(redshift) if include_redshift else 0.0
        processed, _, _, _ = self.processor.process(wave_arr, flux_arr, z_process)
        z_feature = float(redshift) if include_redshift else 0.0
        return np.concatenate(
            [
                np.asarray(processed, dtype=np.float32).reshape(-1),
                np.array([z_feature], dtype=np.float32),
            ]
        )


class LatentEncoderSpectrumProcessor:
    """website_final encoder grid: 1320 linear bins, 3200–9800 Å at 5 Å.

    Training resampled with specutils ``FluxConservingResampler``. This runtime
    uses ``numpy.interp`` onto Settings ``latent_encoder_wavelength_grid`` because
    specutils is not a project dependency. Linear interpolation does not conserve
    flux in a bin and fills interior gaps instead of leaving them empty; bins
    outside the observed wavelength range are marked ignore (mask True).

    Flux is median-absolute normalized (need >= 50 finite bins), clipped to
    [-50, 50], and NaNs filled with 0. Mask True = ignore (DAEP). Phase is not
    included; classifiers pass zeros. ``deredshift=True`` divides lambda by
    (1+z) before resampling (latent_z); ``False`` keeps the observed frame
    (latent_noz).
    """

    def __init__(self, wavelength_grid: Optional[np.ndarray] = None):
        settings = get_settings()
        self.n_wave = settings.latent_encoder_n_wave
        self.min_finite_bins = settings.latent_encoder_min_finite_bins
        self.flux_clip = settings.latent_encoder_flux_clip
        if wavelength_grid is None:
            self.wavelength_grid = np.asarray(
                settings.latent_encoder_wavelength_grid(), dtype=np.float64
            )
        else:
            self.wavelength_grid = np.asarray(wavelength_grid, dtype=np.float64).reshape(-1)
        if self.wavelength_grid.size != self.n_wave:
            raise ValueError(
                f"Encoder grid must have {self.n_wave} bins, got {self.wavelength_grid.size}"
            )

    def process(
        self,
        wave: Union[np.ndarray, list],
        flux: Union[np.ndarray, list],
        redshift: float,
        deredshift: bool,
    ) -> Dict[str, np.ndarray]:
        wave_arr, flux_arr = sort_wave_flux(wave, flux)
        finite = np.isfinite(wave_arr) & np.isfinite(flux_arr)
        wave_arr = wave_arr[finite]
        flux_arr = flux_arr[finite]
        if wave_arr.size < 2:
            raise ValidationError("Spectrum has too few finite samples for encoder resampling")

        if deredshift:
            wave_arr = wave_arr / (1.0 + float(redshift))

        grid = self.wavelength_grid
        # numpy.interp vs FluxConservingResampler: linear sample-to-sample interp
        # onto bin centers; uncovered edges become NaN and are masked ignore.
        resampled = np.interp(grid, wave_arr, flux_arr, left=np.nan, right=np.nan)
        covered = (grid >= wave_arr[0]) & (grid <= wave_arr[-1])
        resampled = np.where(covered, resampled, np.nan)

        finite_bins = np.isfinite(resampled)
        if int(np.count_nonzero(finite_bins)) < self.min_finite_bins:
            raise ValidationError(
                f"Encoder spectrum has fewer than {self.min_finite_bins} finite bins after resample"
            )

        scale = float(np.median(np.abs(resampled[finite_bins])))
        if not np.isfinite(scale) or scale == 0.0:
            raise ValidationError("Cannot median-absolute-normalize encoder flux")

        flux_norm = np.clip(resampled / scale, -self.flux_clip, self.flux_clip)
        mask = ~np.isfinite(flux_norm)
        flux_norm = np.nan_to_num(flux_norm, nan=0.0).astype(np.float32)

        return {
            "flux": flux_norm,
            "wavelength": grid.astype(np.float32),
            "mask": mask,
        }
