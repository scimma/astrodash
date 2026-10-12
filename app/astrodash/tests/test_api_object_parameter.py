"""REST contract tests: the classify endpoint's object parameter is
source-neutral, and the documentation names it identically.

U8 renames the request parameter that carries an object designation from the
catalogue-specific ``oscRef`` to ``objectName``, with no deprecated alias: the
endpoint is gated by ``api_writes_required`` and returns ``403`` in every
deployed environment, so no live consumer can depend on the old name, and the
two documentation files that described it disagreed about its format.

Four things are locked here:

* the view reads ``objectName`` -- the designation reaches the lookup under
  that name and no other (AE17, implementation side);
* ``docs/api/openapi.json`` names the same parameter, so the documented
  contract and the implemented one cannot drift apart without a failure here
  (AE17, documentation side);
* a by-name failure answers with the JSON error envelope and the failure's own
  status rather than an HTML error page (AE7);
* the retired name is not silently accepted -- a request carrying only
  ``oscRef`` is refused as a missing parameter by the real service.

Three further tests cover what the by-name path answers with on failure: the
``409`` ambiguous-designation body carries the matching objects as a list
rather than only inside the message, and ``docs/api/openapi.json`` documents
every status ``POST /process`` can return along with that ``409`` body shape.

A guard scan then proves no file under ``docs/api/`` or ``docs/guides/``
mentions the retired name or the ``osc-`` prefixed identifier format its error
guidance described. ``docs/plans/`` and ``docs/brainstorms/`` are excluded:
the planning artefacts legitimately discuss the retirement.

**The two documentation checks are skipped when the ``docs/`` tree is not
reachable.** The suite normally runs inside the app container, whose image is
built from ``app/`` and which bind-mounts only ``app/`` -- the repository's
``docs/`` directory is not present there. The checks therefore bind the
documented name to the implemented one for anyone running the suite from a
checkout, and skip rather than fail where the tree is genuinely absent.

Like ``test_api_model_type``, these tests patch ``views.API_WRITES_ENABLED``
on so the view body runs, and mock the services where the view body must
complete, so no model weights, database or network access is needed.
"""

import json
import re
import unittest
from pathlib import Path
from typing import List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import Client, SimpleTestCase
from django.urls import reverse

from astrodash.core.exceptions import AmbiguousDesignationException
from astrodash.infrastructure.wiserep.client import WiserepUnreachableError

#: The source-neutral request parameter that carries an object designation.
OBJECT_PARAM = "objectName"

#: The catalogue-specific parameter it replaces. Kept only to assert its
#: absence; nothing in the request path may accept it.
RETIRED_PARAM = "oscRef"

#: The identifier format the retired parameter's error guidance described.
RETIRED_FORMAT_PATTERN = re.compile(r"osc-", re.IGNORECASE)

#: Documentation trees the guard scans, relative to the repository root.
GUARDED_DOC_DIRS = ("docs/api", "docs/guides")

#: The schema file whose parameter name must match the one the view reads.
OPENAPI_RELPATH = "docs/api/openapi.json"

#: The keys every entry in the 409 response's ``objects`` list carries. A bare
#: designation asks someone to pick between things they cannot tell apart, so
#: the type and redshift travel with it.
AMBIGUOUS_OBJECT_KEYS = {"designation", "object_type", "redshift"}

#: The statuses POST /process can answer with, each of which the schema file
#: must document. The by-name path contributes 404, 409, 422 and 502; three
#: distinct failure-class families deliberately carry distinct statuses.
PROCESS_STATUSES = ("200", "400", "403", "404", "409", "422", "500", "502")


def _find_repo_root() -> Optional[Path]:
    """Locate the repository root by its documentation tree.

    Returns:
        The first ancestor directory that holds :data:`OPENAPI_RELPATH`, or
        ``None`` when no ancestor does -- which is the case inside the app
        container, where only ``app/`` is present.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / OPENAPI_RELPATH).is_file():
            return candidate
    return None


REPO_ROOT = _find_repo_root()
DOCS_SKIP_REASON = (
    f"{OPENAPI_RELPATH} is not reachable from {Path(__file__).resolve()}; "
    "the documentation tree is not present in the app container"
)


def _mock_services() -> Tuple[MagicMock, MagicMock, MagicMock]:
    """Build mocked spectrum/processing/classification services.

    Returns:
        A ``(spectrum_svc, processing_svc, classification_svc)`` tuple whose
        async methods are ``AsyncMock``s returning payloads the classify view
        can serialize.
    """
    processed = MagicMock()
    processed.x = [4000.0, 6000.0, 9000.0]
    processed.y = [1.0, 2.0, 1.0]
    processed.redshift = 0.0

    classification = MagicMock()
    classification.model_type = "dash"
    classification.results = {"best_matches": []}

    spectrum_svc = MagicMock(
        get_spectrum_data=AsyncMock(return_value=MagicMock()),
        save_spectrum=AsyncMock(return_value=None),
    )
    processing_svc = MagicMock(
        process_spectrum_with_params=AsyncMock(return_value=processed)
    )
    classification_svc = MagicMock(
        classify_spectrum=AsyncMock(return_value=classification)
    )
    return spectrum_svc, processing_svc, classification_svc


def _scan_docs(root: Path) -> List[str]:
    """Scan the guarded documentation trees for retired-parameter mentions.

    Args:
        root: The repository root holding the ``docs/`` tree.

    Returns:
        A list of ``path:lineno: line`` strings -- one per offending line.
        Empty when every guarded file is clean.
    """
    offenders: List[str] = []
    for relative in GUARDED_DOC_DIRS:
        for path in sorted((root / relative).rglob("*")):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if RETIRED_PARAM in line or RETIRED_FORMAT_PATTERN.search(line):
                    offenders.append(
                        f"  {path.relative_to(root)}:{lineno}: {line.strip()}"
                    )
    return offenders


class ObjectParameterContractTests(SimpleTestCase):
    """The view reads ``objectName``, and no alias for the old name remains."""

    def setUp(self) -> None:
        self.client = Client()
        self.url = reverse("astrodash_api:process_spectrum")

    def _post(self, data: dict, spectrum_svc: Optional[MagicMock] = None):
        """POST to the classify endpoint with writes enabled and services mocked.

        Args:
            data: The multipart form fields to send.
            spectrum_svc: An override for the spectrum service, so a test can
                supply one whose lookup fails.

        Returns:
            A ``(response, spectrum_svc)`` tuple.
        """
        mocked_spectrum, processing_svc, classification_svc = _mock_services()
        spectrum_svc = spectrum_svc or mocked_spectrum
        with patch("astrodash.views.API_WRITES_ENABLED", True), patch(
            "astrodash.views.get_spectrum_service", return_value=spectrum_svc
        ), patch(
            "astrodash.views.get_spectrum_processing_service",
            return_value=processing_svc,
        ), patch(
            "astrodash.views.get_classification_service",
            return_value=classification_svc,
        ):
            return self.client.post(self.url, data=data), spectrum_svc

    def test_view_reads_the_source_neutral_object_parameter(self) -> None:
        """AE17: a designation sent as ``objectName`` reaches the lookup."""
        params = json.dumps({"modelType": "dash", OBJECT_PARAM: "2002er"})
        resp, spectrum_svc = self._post({"params": params})

        self.assertEqual(resp.status_code, 200)
        spectrum_svc.get_spectrum_data.assert_awaited_once()
        self.assertEqual(
            spectrum_svc.get_spectrum_data.await_args.kwargs["object_name"],
            "2002er",
        )

    @unittest.skipIf(REPO_ROOT is None, DOCS_SKIP_REASON)
    def test_schema_file_names_the_parameter_the_view_reads(self) -> None:
        """AE17: the schema file's parameter is the one the view reads.

        Renaming it on either side alone breaks this pair: the view-side test
        above fails if the view changes, this one if the schema file does.
        """
        schema_path = REPO_ROOT / OPENAPI_RELPATH
        schema_text = schema_path.read_text(encoding="utf-8")
        described = json.loads(schema_text)["paths"]["/process"]["post"]["requestBody"][
            "content"
        ]["multipart/form-data"]["schema"]["properties"]["params"]["description"]
        self.assertIn(OBJECT_PARAM, described)
        self.assertNotIn(RETIRED_PARAM, schema_text)

    def test_by_name_failure_returns_the_json_error_envelope(self) -> None:
        """AE7: an unreachable source answers in JSON, not as an HTML page."""
        failing = MagicMock(
            get_spectrum_data=AsyncMock(
                side_effect=WiserepUnreachableError(
                    "export for '2002er' exceeded the 120-second deadline"
                )
            ),
            save_spectrum=AsyncMock(return_value=None),
        )
        params = json.dumps({"modelType": "dash", OBJECT_PARAM: "2002er"})
        resp, _ = self._post({"params": params}, spectrum_svc=failing)

        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp["Content-Type"], "application/json")
        self.assertIn("WISeREP", resp.json()["detail"])

    def test_ambiguous_designation_returns_the_matching_objects(self) -> None:
        """The 409 body carries a machine-readable list of the matches.

        ``AmbiguousDesignationException`` holds the matching objects, and the
        documented contract promises them in the response, so a client can
        offer the choice without parsing designations back out of ``detail``.
        """
        matches = (
            {"designation": "SN 2002ea", "object_type": "SN Ia", "redshift": 0.0279},
            {"designation": "SN 2002eb", "object_type": "", "redshift": None},
        )
        failing = MagicMock(
            get_spectrum_data=AsyncMock(
                side_effect=AmbiguousDesignationException("2002e", matches)
            ),
            save_spectrum=AsyncMock(return_value=None),
        )
        params = json.dumps({"modelType": "dash", OBJECT_PARAM: "2002e"})
        resp, _ = self._post({"params": params}, spectrum_svc=failing)

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp["Content-Type"], "application/json")
        body = resp.json()
        self.assertIn("matches more than one object", body["detail"])
        self.assertEqual(len(body["objects"]), 2)
        for entry in body["objects"]:
            self.assertEqual(set(entry), AMBIGUOUS_OBJECT_KEYS)
        self.assertEqual(
            [entry["designation"] for entry in body["objects"]],
            ["SN 2002ea", "SN 2002eb"],
        )
        self.assertEqual(body["objects"][0]["object_type"], "SN Ia")
        self.assertEqual(body["objects"][0]["redshift"], 0.0279)
        self.assertEqual(body["objects"][1]["object_type"], "")
        self.assertIsNone(body["objects"][1]["redshift"])

    @unittest.skipIf(REPO_ROOT is None, DOCS_SKIP_REASON)
    def test_schema_file_documents_every_process_status(self) -> None:
        """The schema file documents each status POST /process can answer with.

        The five by-name failure conditions carry five distinct statuses by
        design; a schema that documents only some of them tells a client to
        treat the rest as unexpected.
        """
        schema_path = REPO_ROOT / OPENAPI_RELPATH
        documented = json.loads(schema_path.read_text(encoding="utf-8"))["paths"][
            "/process"
        ]["post"]["responses"]
        for status in PROCESS_STATUSES:
            self.assertIn(status, documented)

    @unittest.skipIf(REPO_ROOT is None, DOCS_SKIP_REASON)
    def test_schema_file_documents_the_ambiguous_body_shape(self) -> None:
        """The documented 409 body is the one the view serializes."""
        schema = json.loads((REPO_ROOT / OPENAPI_RELPATH).read_text(encoding="utf-8"))
        ref = schema["paths"]["/process"]["post"]["responses"]["409"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        name = ref.rsplit("/", 1)[-1]
        documented = schema["components"]["schemas"][name]["properties"]
        self.assertIn("detail", documented)
        self.assertEqual(
            set(documented["objects"]["items"]["properties"]),
            AMBIGUOUS_OBJECT_KEYS,
        )

    def test_retired_parameter_is_not_accepted(self) -> None:
        """The old name is refused as missing, not read as a designation.

        No alias remains (KTD9), so a request carrying only ``oscRef`` reaches
        the real lookup with no designation at all and returns its
        missing-parameter refusal. The services are deliberately not mocked
        here: an alias would otherwise be hidden behind a stub.
        """
        params = json.dumps({"modelType": "dash", RETIRED_PARAM: "2002er"})
        with patch("astrodash.views.API_WRITES_ENABLED", True):
            resp = self.client.post(self.url, data={"params": params})

        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp["Content-Type"], "application/json")
        self.assertIn("object designation", resp.json()["detail"])


class RetiredObjectParameterDocGuardTests(SimpleTestCase):
    """No API or guide documentation may reintroduce the retired parameter."""

    @unittest.skipIf(REPO_ROOT is None, DOCS_SKIP_REASON)
    def test_no_guarded_doc_mentions_the_retired_parameter(self) -> None:
        offenders = _scan_docs(REPO_ROOT)
        self.assertFalse(
            offenders,
            f"Found the retired {RETIRED_PARAM!r} parameter or its 'osc-' "
            f"prefixed identifier format in API documentation. The object "
            f"parameter is {OBJECT_PARAM!r} and carries a WISeREP "
            f"designation:\n" + "\n".join(offenders),
        )

    def test_guard_flags_a_reintroduced_mention(self) -> None:
        """The guard's rule fires on a regression and passes the new name.

        Proven without the tree, so it holds in the container too: a future
        edit cannot weaken the rule to a no-op unnoticed.
        """
        retired_row = "| `oscRef` | String | - | SN name on the catalogue |"
        self.assertTrue(
            RETIRED_PARAM in retired_row or RETIRED_FORMAT_PATTERN.search(retired_row)
        )
        self.assertTrue(RETIRED_FORMAT_PATTERN.search("ensure it uses osc-<object>-0"))

        current_row = "| `objectName` | String | - | Object designation |"
        self.assertNotIn(RETIRED_PARAM, current_row)
        self.assertIsNone(RETIRED_FORMAT_PATTERN.search(current_row))
