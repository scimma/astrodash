from collections.abc import Mapping, Sequence
from http import HTTPStatus

HTTP_400_BAD_REQUEST = HTTPStatus.BAD_REQUEST.value
HTTP_404_NOT_FOUND = HTTPStatus.NOT_FOUND.value
HTTP_500_INTERNAL_SERVER_ERROR = HTTPStatus.INTERNAL_SERVER_ERROR.value
HTTP_422_UNPROCESSABLE_ENTITY = HTTPStatus.UNPROCESSABLE_ENTITY.value
HTTP_409_CONFLICT = HTTPStatus.CONFLICT.value
HTTP_502_BAD_GATEWAY = HTTPStatus.BAD_GATEWAY.value

class AppException(Exception):
    """Base exception for the application."""
    def __init__(self, message: str, status_code: int = HTTP_400_BAD_REQUEST):
        self.message = message
        self.status_code = status_code
        super().__init__(message)

# Data-related exceptions
class SpectrumNotFoundException(AppException):
    """Raised when a spectrum is not found."""
    def __init__(self, spectrum_id: str):
        super().__init__(f"Spectrum with ID '{spectrum_id}' not found.", status_code=HTTP_404_NOT_FOUND)

class ModelNotFoundException(AppException):
    """Raised when a model is not found."""
    def __init__(self, model_id: str):
        super().__init__(f"Model with ID '{model_id}' not found.", status_code=HTTP_404_NOT_FOUND)

class TemplateNotFoundException(AppException):
    """Raised when a template is not found."""
    def __init__(self, sn_type: str, age_bin: str = None):
        if age_bin:
            message = f"Template not found for SN type '{sn_type}' and age bin '{age_bin}'."
        else:
            message = f"Template not found for SN type '{sn_type}'."
        super().__init__(message, status_code=HTTP_404_NOT_FOUND)

class LineListNotFoundException(AppException):
    """Raised when the line list file is not found."""
    def __init__(self, file_path: str = None):
        message = "Line list file not found. Please ensure the sneLineList.txt file is available."
        if file_path:
            message += f" Expected at: {file_path}"
        super().__init__(message, status_code=HTTP_404_NOT_FOUND)

class ElementNotFoundException(AppException):
    """Raised when an element is not found in the line list."""
    def __init__(self, element: str):
        super().__init__(f"Element '{element}' not found in line list.", status_code=HTTP_404_NOT_FOUND)

# Processing and classification exceptions
class ClassificationException(AppException):
    """Raised for errors during classification."""
    def __init__(self, message: str = "Classification failed."):
        super().__init__(message, status_code=HTTP_400_BAD_REQUEST)

class SpectrumProcessingException(AppException):
    """Raised for errors during spectrum processing."""
    def __init__(self, message: str = "Spectrum processing failed."):
        super().__init__(message, status_code=HTTP_400_BAD_REQUEST)

class ModelProcessingException(AppException):
    """Raised for errors during model processing."""
    def __init__(self, message: str = "Model processing failed."):
        super().__init__(message, status_code=HTTP_400_BAD_REQUEST)

class BatchProcessingException(AppException):
    """Raised for errors during batch processing."""
    def __init__(self, message: str = "Batch processing failed."):
        super().__init__(message, status_code=HTTP_400_BAD_REQUEST)

# Validation exceptions
class ValidationException(AppException):
    """Raised for validation errors."""
    def __init__(self, message: str = "Validation failed."):
        super().__init__(message, status_code=HTTP_422_UNPROCESSABLE_ENTITY)

class FileValidationException(ValidationException):
    """Raised for file validation errors."""
    def __init__(self, message: str = "File validation failed."):
        super().__init__(message)

class ModelValidationException(ValidationException):
    """Raised for model validation errors."""
    def __init__(self, message: str = "Model validation failed."):
        super().__init__(message)

class SpectrumValidationException(ValidationException):
    """Raised for spectrum validation errors."""
    def __init__(self, message: str = "Spectrum validation failed."):
        super().__init__(message)

# Storage and file exceptions
class StorageException(AppException):
    """Raised for storage-related errors."""
    def __init__(self, message: str = "Storage error."):
        super().__init__(message, status_code=HTTP_500_INTERNAL_SERVER_ERROR)

class FileNotFoundException(AppException):
    """Raised when a file is not found."""
    def __init__(self, file_path: str):
        super().__init__(f"File not found: {file_path}", status_code=HTTP_404_NOT_FOUND)

class FileReadException(AppException):
    """Raised when there's an error reading a file."""
    def __init__(self, file_path: str, error: str = None):
        message = f"Error reading file: {file_path}"
        if error:
            message += f" - {error}"
        super().__init__(message, status_code=HTTP_400_BAD_REQUEST)

class UnsupportedFileFormatException(AppException):
    """Raised when an unsupported file format is provided."""
    def __init__(self, file_format: str, supported_formats: list = None):
        message = f"Unsupported file format: {file_format}"
        if supported_formats:
            message += f". Supported formats: {', '.join(supported_formats)}"
        super().__init__(message, status_code=HTTP_400_BAD_REQUEST)

# Configuration and setup exceptions
class ConfigurationException(AppException):
    """Raised for configuration errors."""
    def __init__(self, message: str = "Configuration error."):
        super().__init__(message, status_code=HTTP_500_INTERNAL_SERVER_ERROR)

class ModelConfigurationException(ConfigurationException):
    """Raised for model configuration errors."""
    def __init__(self, message: str = "Model configuration error."):
        super().__init__(message)

# External service exceptions
class ExternalServiceException(AppException):
    """Raised for external service errors."""
    def __init__(self, service: str, message: str = None):
        if message:
            error_message = f"{service} service error: {message}"
        else:
            error_message = f"{service} service error."
        super().__init__(error_message, status_code=HTTP_500_INTERNAL_SERVER_ERROR)

class AmbiguousDesignationException(AppException):
    """Raised when a submitted designation matches more than one object.

    The by-name lookup refuses rather than guessing which object the
    researcher meant, and carries the matching objects so the surface can offer
    a choice the researcher can actually make. The type and redshift travel with
    each designation because a list of bare names asks someone to pick between
    things they cannot tell apart.
    """

    def __init__(self, name: str, objects: Sequence[Mapping[str, object]]) -> None:
        """Build the failure.

        Args:
            name: The designation the researcher submitted.
            objects: The distinct objects the source matched, each a mapping of
                ``designation`` as the source spells it, ``object_type`` and
                ``redshift``. The latter two may be empty or None when the
                source records none.
        """
        self.name = name
        self.objects = tuple(dict(item) for item in objects)
        self.designations = tuple(
            str(item.get("designation", "")) for item in self.objects
        )
        listed = ", ".join(self.designations)
        super().__init__(
            f"Designation '{name}' matches more than one object ({listed}). "
            f"Choose which one to classify.",
            status_code=HTTP_409_CONFLICT,
        )


class NoEligibleSpectrumException(AppException):
    """Raised when a designation resolved but carries no spectrum to classify.

    Distinct from the source being unreachable: the source answered, and what
    it holds for this designation is either embargoed, not a spectrum of the
    object itself, or absent.
    """

    def __init__(self, name: str, detail: str = None) -> None:
        """Build the failure.

        Args:
            name: The designation the researcher submitted.
            detail: Optional extra explanation appended to the message.
        """
        self.name = name
        message = f"No public, classifiable spectrum is available for '{name}'."
        if detail:
            message += f" {detail}"
        super().__init__(message, status_code=HTTP_404_NOT_FOUND)


class IncompleteSourceExportException(AppException):
    """Raised when the source's export could not be read to exhaustion.

    Ranking is only correct over every eligible spectrum for a designation, so
    a truncated export is refused rather than ranked from the pages already
    fetched. Distinct from the source being unreachable: the pages that arrived
    were readable, but the set they form is partial.
    """

    def __init__(self, name: str, detail: str = None) -> None:
        """Build the failure.

        Args:
            name: The designation the researcher submitted.
            detail: Optional explanation of where the export stopped.
        """
        self.name = name
        message = (
            f"The spectrum listing for '{name}' could not be read in full, so "
            f"no epoch was selected."
        )
        if detail:
            message += f" {detail}"
        super().__init__(message, status_code=HTTP_502_BAD_GATEWAY)

# Resource and constraint exceptions
class ResourceNotFoundException(AppException):
    """Raised when a resource is not found."""
    def __init__(self, resource_type: str, resource_id: str):
        super().__init__(f"{resource_type} with ID '{resource_id}' not found.", status_code=HTTP_404_NOT_FOUND)

class ResourceConflictException(AppException):
    """Raised when there's a conflict with a resource."""
    def __init__(self, message: str = "Resource conflict."):
        super().__init__(message, status_code=HTTP_409_CONFLICT)

class ModelConflictException(ResourceConflictException):
    """Raised when there's a conflict with a model (e.g., duplicate name)."""
    def __init__(self, model_name: str):
        super().__init__(f"Model with name '{model_name}' already exists.")
