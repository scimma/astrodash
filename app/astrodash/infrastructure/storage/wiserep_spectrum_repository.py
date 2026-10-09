"""Reads one chosen WISeREP spectrum into a domain spectrum.

The ASCII download URL this repository fetches arrives inside WISeREP's
export, which is third-party content. An unchecked fetch would therefore let
that content choose the request target of the server itself, so every URL --
the one the export carried and the target of any redirect answering it -- is
accepted only when its scheme is ``https`` and its host is the configured
WISeREP host. A URL failing either check is refused as unusable rather than
fetched, and no outbound request is made for it.

Parsing reuses the extension-dispatched readers in
:mod:`astrodash.infrastructure.storage.file_spectrum_repository`, which keep
only the 4000-9000 Angstrom window the classifiers model. A download leaving
too few points inside that window is refused as unusable instead of being
classified, and a missing redshift stays missing: nothing here substitutes
zero for one WISeREP does not record.

Certificate verification is left at the library default; nothing here disables
it.
"""

from __future__ import annotations

import io
import logging
from functools import partial
from posixpath import basename
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit

import requests
from django.utils import timezone

from astrodash.config.settings import Settings, get_settings
from astrodash.core.exceptions import SpectrumValidationException
from astrodash.domain.models.spectrum import Spectrum
from astrodash.domain.repositories.spectrum_repository import SpectrumRepository
from astrodash.infrastructure.storage.file_spectrum_repository import (
    FileSpectrumRepository,
)
from astrodash.infrastructure.wiserep.client import WiserepClient, make_session
from astrodash.infrastructure.wiserep.selection import SpectrumCandidate
from astrodash.models import SOURCE_NAME_WISEREP

logger = logging.getLogger(__name__)

#: The window the readers keep, in Angstroms. Points outside it are dropped
#: before this repository counts what is left.
WINDOW_MIN_ANGSTROMS = 4000.0
WINDOW_MAX_ANGSTROMS = 9000.0

#: The fewest in-window points a spectrum may carry and still be classified
#: (R21). Below this the download is reported as unusable.
MIN_WINDOW_POINTS = 100

#: The most redirects one download may follow. Every target is host-checked,
#: so this only bounds a chain that stays on the configured host.
MAX_REDIRECTS = 3

#: The scheme a spectrum URL must use.
_REQUIRED_SCHEME = "https"


class WiserepUnusableSpectrumError(SpectrumValidationException):
    """Raised when a chosen spectrum cannot be classified as fetched.

    Covers a download URL that is not on the configured WISeREP host over
    ``https``, a redirect leading off it, a payload the readers cannot parse
    as spectrum data, and a spectrum leaving fewer than
    :data:`MIN_WINDOW_POINTS` points inside the usable window. It is
    deliberately distinct from WISeREP being unreachable.

    The message says which spectrum was being read and why it is unusable.
    """


def allowed_host(settings: Settings) -> str:
    """The one host a spectrum download may be read from.

    Args:
        settings: The application settings carrying ``wiserep_base_url``.

    Returns:
        The configured WISeREP host, lowercased.
    """
    return (urlsplit(settings.wiserep_base_url).hostname or "").lower()


def require_allowed_url(url: str, host: str, what: str = "spectrum URL") -> str:
    """Accept a URL only when it addresses the configured host over https.

    Args:
        url: The URL to check.
        host: The one host accepted, lowercased.
        what: A phrase naming what is being checked, used in the failure
            message.

    Returns:
        The URL, unchanged, when it is acceptable.

    Raises:
        WiserepUnusableSpectrumError: When the scheme is not ``https``, when
            the URL carries credentials, or when its host is anything other
            than ``host``.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() != _REQUIRED_SCHEME:
        raise WiserepUnusableSpectrumError(
            f"{what} {url!r} is not {_REQUIRED_SCHEME}; refusing to fetch it"
        )
    if parts.username or parts.password:
        raise WiserepUnusableSpectrumError(
            f"{what} {url!r} carries credentials; refusing to fetch it"
        )
    target = (parts.hostname or "").lower()
    if target != host:
        raise WiserepUnusableSpectrumError(
            f"{what} {url!r} is not on {host}; refusing to fetch it"
        )
    return url


def _refuse_offsite_redirect(
    host: str, response: requests.Response, *args: Any, **kwargs: Any
) -> requests.Response:
    """Refuse a redirect whose target leaves the configured host.

    Installed as a ``requests`` response hook, which runs before the redirect
    is resolved, so an off-host target is never requested.

    Args:
        host: The one host accepted, lowercased.
        response: The response just received.
        *args: Positional hook arguments, ignored.
        **kwargs: Keyword hook arguments, ignored.

    Returns:
        The response, unchanged, when it is not an off-host redirect.

    Raises:
        WiserepUnusableSpectrumError: When the response redirects to a target
            that is not on ``host`` over ``https``.
    """
    location = response.headers.get("Location")
    if not location or not response.is_redirect:
        return response
    require_allowed_url(urljoin(response.url, location), host, what="redirect target")
    return response


def make_host_pinned_session(
    host: str, max_redirects: int = MAX_REDIRECTS
) -> requests.Session:
    """Build a session that will not be redirected off the configured host.

    Args:
        host: The one host accepted, lowercased.
        max_redirects: The most redirects one request may follow. Bounds a
            chain that stays on ``host``; a chain leaving it is refused
            outright.

    Returns:
        A session carrying the shared identity and retry adapter, with the
        redirect check installed and certificate verification left at its
        default.
    """
    session = make_session()
    session.max_redirects = max_redirects
    session.hooks["response"].append(partial(_refuse_offsite_redirect, host))
    return session


class _NamedPayload(io.BytesIO):
    """Downloaded bytes presented to the readers as a named file."""

    def __init__(self, payload: bytes, name: str) -> None:
        """Wrap the payload.

        Args:
            payload: The downloaded file's bytes.
            name: The file name the readers dispatch on.
        """
        super().__init__(payload)
        self.name = name


class _TransientFileReader(FileSpectrumRepository):
    """The file readers, reused without persisting what they parse.

    Retention of a fetched spectrum is the caller's decision, so ``save`` here
    hands the parsed spectrum straight back and no storage directory is
    created.
    """

    def __init__(self) -> None:
        """Build the reader, deliberately holding no storage."""

    def save(self, spectrum: Spectrum) -> Spectrum:
        """Return the spectrum without storing it.

        Args:
            spectrum: The spectrum the readers just parsed.

        Returns:
            That same spectrum.
        """
        return spectrum


class WiserepSpectrumRepository(SpectrumRepository):
    """Fetches the spectrum a chosen WISeREP candidate points at."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: WiserepClient | None = None,
    ) -> None:
        """Build the repository.

        Args:
            settings: The application settings carrying ``wiserep_base_url``,
                which fixes the one host a download may be read from. Loaded
                from the environment when omitted.
            client: The bounded WISeREP transport to download through. One is
                built over a host-pinned session when omitted.
        """
        self._settings = settings or get_settings()
        self._host = allowed_host(self._settings)
        self._client = client or WiserepClient(
            settings=self._settings,
            session=make_host_pinned_session(self._host),
        )
        self._reader = _TransientFileReader()

    def get_for_candidate(self, candidate: SpectrumCandidate) -> Spectrum:
        """Download and read the spectrum a candidate points at.

        Args:
            candidate: The chosen eligible spectrum, carrying its ASCII
                download URL, the identifiers that locate it upstream and the
                object's recorded redshift.

        Returns:
            A spectrum carrying the candidate's redshift, which stays ``None``
            when WISeREP records none, together with the object designation,
            the WISeREP spectrum identifier, the source name and the retrieval
            time.

        Raises:
            WiserepUnusableSpectrumError: When the candidate's URL or a
                redirect answering it is not on the configured host over
                ``https``, when the payload cannot be read as spectrum data,
                or when fewer than :data:`MIN_WINDOW_POINTS` points fall
                inside the 4000-9000 Angstrom window.
            WiserepUnreachableError: When the download breached one of the
                transport's bounds.
        """
        url = require_allowed_url(candidate.ascii_url, self._host)
        payload = self._client.download_spectrum(url)

        file_name = basename(urlsplit(url).path) or candidate.spectrum_id
        parsed = self._reader.get_from_file(_NamedPayload(payload, file_name))
        if parsed is None or not parsed.x:
            raise WiserepUnusableSpectrumError(
                f"spectrum {candidate.spectrum_id} for "
                f"{candidate.designation!r} could not be read as spectrum "
                f"data from {file_name!r}"
            )

        in_window = self._count_in_window(parsed.x)
        if in_window < MIN_WINDOW_POINTS:
            raise WiserepUnusableSpectrumError(
                f"spectrum {candidate.spectrum_id} for "
                f"{candidate.designation!r} leaves {in_window} point(s) "
                f"inside {WINDOW_MIN_ANGSTROMS:.0f}-{WINDOW_MAX_ANGSTROMS:.0f} "
                f"Angstroms, below the {MIN_WINDOW_POINTS}-point minimum"
            )

        logger.info(
            "Read WISeREP spectrum %s for %r: %d in-window point(s)",
            candidate.spectrum_id,
            candidate.designation,
            in_window,
        )
        return Spectrum(
            x=parsed.x,
            y=parsed.y,
            redshift=candidate.redshift,
            object_designation=candidate.designation,
            source_name=SOURCE_NAME_WISEREP,
            source_spectrum_id=candidate.spectrum_id,
            retrieved_at=timezone.now(),
            file_name=file_name,
            meta={
                "source": SOURCE_NAME_WISEREP,
                "spectrum_id": candidate.spectrum_id,
                "observation_date": candidate.observation_date,
                "telescope": candidate.telescope,
                "instrument": candidate.instrument,
                "ascii_url": url,
            },
        )

    @staticmethod
    def _count_in_window(wavelengths: list[float]) -> int:
        """Count the wavelengths falling inside the usable window.

        Args:
            wavelengths: The parsed wavelength values.

        Returns:
            How many of them lie between :data:`WINDOW_MIN_ANGSTROMS` and
            :data:`WINDOW_MAX_ANGSTROMS` inclusive.
        """
        return sum(
            1
            for value in wavelengths
            if WINDOW_MIN_ANGSTROMS <= value <= WINDOW_MAX_ANGSTROMS
        )

    def save(self, spectrum: Spectrum) -> Spectrum:
        """Reject saving; this repository only reads from WISeREP.

        Args:
            spectrum: The spectrum offered for storage.

        Returns:
            Never returns.

        Raises:
            NotImplementedError: Always. Retention belongs to the storage
                repository.
        """
        raise NotImplementedError(
            "WiserepSpectrumRepository reads from WISeREP and does not save"
        )

    def get_by_id(self, spectrum_id: str) -> Optional[Spectrum]:
        """Look a spectrum up by storage identifier.

        Args:
            spectrum_id: An internal storage identifier.

        Returns:
            ``None``; WISeREP is addressed by designation and spectrum
            identifier, not by storage identifier.
        """
        return None

    def get_by_osc_ref(self, osc_ref: str) -> Optional[Spectrum]:
        """Look a spectrum up by catalogue reference.

        Args:
            osc_ref: A catalogue reference.

        Returns:
            ``None``; this repository consults WISeREP only.
        """
        return None

    def get_from_file(self, file: Any) -> Optional[Spectrum]:
        """Read a spectrum from an uploaded file.

        Args:
            file: An uploaded file object.

        Returns:
            ``None``; uploads are read by the file repository.
        """
        return None
