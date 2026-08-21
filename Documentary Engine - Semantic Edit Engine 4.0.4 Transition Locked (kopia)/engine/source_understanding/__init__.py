"""Public pure-domain Source Understanding foundation API."""

from ._canonical_json import (
    SI01Error,
    SI01InvalidFieldError,
    SI01InvalidYouTubeReferenceError,
    SI01MalformedDataError,
    SI01SizeLimitError,
    SI01UnsupportedVersionError,
)
from .source_identity import (
    CanonicalSourceIdentity,
    SourceKind,
    SourceObservationIdentity,
    SourceReference,
    canonicalize_youtube_reference,
)

__all__ = [
    "SourceKind",
    "SourceReference",
    "CanonicalSourceIdentity",
    "SourceObservationIdentity",
    "canonicalize_youtube_reference",
    "SI01Error",
    "SI01MalformedDataError",
    "SI01UnsupportedVersionError",
    "SI01SizeLimitError",
    "SI01InvalidFieldError",
    "SI01InvalidYouTubeReferenceError",
]
