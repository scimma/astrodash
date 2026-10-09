from datetime import datetime
from typing import List, Optional

class Spectrum:
    """
    Domain model representing a scientific spectrum.
    This model is independent of storage, API, or infrastructure concerns.
    """
    def __init__(
        self,
        x: List[float],
        y: List[float],
        redshift: Optional[float] = None,
        id: Optional[str] = None,
        object_designation: Optional[str] = None,
        source_name: Optional[str] = None,
        source_spectrum_id: Optional[str] = None,
        retrieved_at: Optional[datetime] = None,
        file_name: Optional[str] = None,
        meta: Optional[dict] = None
    ):
        """Initialize a spectrum.

        Args:
            x: Wavelength values.
            y: Flux values.
            redshift: Redshift of the object, when known.
            id: Storage identifier of this spectrum.
            object_designation: Designation of the object the spectrum belongs
                to, as submitted or as the source records it.
            source_name: Name of the source the spectrum came from.
            source_spectrum_id: The source's own identifier for this spectrum,
                which distinguishes epochs of the same object.
            retrieved_at: Time the spectrum was retrieved from its source.
            file_name: Original file name, when the spectrum came from a file.
            meta: Additional metadata.
        """
        self.id = id
        self.object_designation = object_designation
        self.source_name = source_name
        self.source_spectrum_id = source_spectrum_id
        self.retrieved_at = retrieved_at
        self.file_name = file_name
        self.x = x  # Wavelengths
        self.y = y  # Flux values
        self.redshift = redshift
        self.meta = meta or {}

    def __repr__(self):
        return (
            f"Spectrum(id={self.id}, object_designation={self.object_designation}, "
            f"source_name={self.source_name}, "
            f"source_spectrum_id={self.source_spectrum_id}, "
            f"file_name={self.file_name}, "
            f"x_len={len(self.x)}, y_len={len(self.y)}, redshift={self.redshift})"
        )
