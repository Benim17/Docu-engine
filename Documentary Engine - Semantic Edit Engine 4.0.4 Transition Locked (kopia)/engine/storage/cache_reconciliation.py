"""Internal H2A read-only cache and catalog identity discovery."""

from __future__ import annotations

import errno
import hashlib
import os
import re
from dataclasses import dataclass
from enum import Enum, IntFlag
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

from .cache_catalog import (
    CACHE_CATALOG_LAYOUT_VERSION,
    MAX_CATALOG_RECORD_BYTES,
    CacheCatalogContractError,
    CacheCatalogIdentity,
    CacheCatalogLiveRecord,
    CacheCatalogLookupStatus,
    CacheCatalogReadOnlyBackend,
    CacheCatalogRecord,
    CacheCatalogTombstone,
    CacheCatalogUnsupportedVersionError,
    parse_cache_catalog_record,
)
from .cache_lookup import (
    DEFAULT_MAX_METADATA_BYTES,
    BoundedFileRead,
    CacheLookupFilesystemError,
    FileIdentity,
    FilesystemObjectType,
    LocalReadOnlyCacheFilesystem,
    SymlinkRejectedError,
    UnstableFilesystemObjectError,
    UnsupportedFilesystemObjectError,
    ValidatedCacheRoot,
)
from .persistent_cache import CacheEntryContractError, CacheEntryMetadata, CacheNamespace


MAX_RECONCILIATION_IDENTITIES_PER_RUN = 1_024
MAX_RECONCILIATION_DIRECTORY_LISTINGS = 4_096
MAX_RECONCILIATION_DIRECTORY_ENTRIES = 4_096
MAX_RECONCILIATION_PAGE_ITEMS = 256
MAX_RECONCILIATION_RELATIVE_PATH_UTF8_BYTES = 1_024
MAX_RECONCILIATION_TRAVERSAL_DEPTH = 64
RECONCILIATION_CURSOR_VERSION = 1

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SHARD = re.compile(r"^[0-9a-f]{2}$")
_WRITER_TOKEN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
_CATALOG_RECORD = re.compile(r"^([0-9a-f]{64})\.json$")
_CATALOG_TEMP = re.compile(r"^\.catalog-tmp-[0-9a-f]{32}$")


class ReconciliationDiscoveryError(RuntimeError):
    """Fail-closed H2A discovery error."""


class ReconciliationDiscoveryLimitError(ReconciliationDiscoveryError):
    """A locked H2A discovery limit was exceeded."""


class CacheCatalogReconciliationMode(str, Enum):
    INCREMENTAL_IDENTITIES = "incremental_identities"
    FULL_IN_PLACE = "full_in_place"


class ReconciliationDiscoverySource(str, Enum):
    FINAL = "final"
    STAGING = "staging"
    CATALOG = "catalog"


class ReconciliationSourceFlags(IntFlag):
    FINAL = 1
    STAGING = 2
    CATALOG = 4


class CatalogSlotClassification(str, Enum):
    SUPPORTED_LIVE = "supported_live"
    SUPPORTED_TOMBSTONE = "supported_tombstone"
    CORRUPT = "corrupt"
    UNSUPPORTED = "unsupported"


def _bounded_positive(value: object, name: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer from 1 through {maximum}.")
    return value


@dataclass(frozen=True)
class ReconciliationDiscoveryPolicy:
    max_identities_per_run: int = MAX_RECONCILIATION_IDENTITIES_PER_RUN
    max_directory_listings: int = MAX_RECONCILIATION_DIRECTORY_LISTINGS
    max_entries_per_directory: int = MAX_RECONCILIATION_DIRECTORY_ENTRIES
    page_size: int = MAX_RECONCILIATION_PAGE_ITEMS
    max_relative_path_utf8_bytes: int = MAX_RECONCILIATION_RELATIVE_PATH_UTF8_BYTES
    max_traversal_depth: int = MAX_RECONCILIATION_TRAVERSAL_DEPTH

    def __post_init__(self) -> None:
        _bounded_positive(
            self.max_identities_per_run,
            "max_identities_per_run",
            MAX_RECONCILIATION_IDENTITIES_PER_RUN,
        )
        _bounded_positive(
            self.max_directory_listings,
            "max_directory_listings",
            MAX_RECONCILIATION_DIRECTORY_LISTINGS,
        )
        _bounded_positive(
            self.max_entries_per_directory,
            "max_entries_per_directory",
            MAX_RECONCILIATION_DIRECTORY_ENTRIES,
        )
        _bounded_positive(self.page_size, "page_size", MAX_RECONCILIATION_PAGE_ITEMS)
        _bounded_positive(
            self.max_relative_path_utf8_bytes,
            "max_relative_path_utf8_bytes",
            MAX_RECONCILIATION_RELATIVE_PATH_UTF8_BYTES,
        )
        _bounded_positive(
            self.max_traversal_depth,
            "max_traversal_depth",
            MAX_RECONCILIATION_TRAVERSAL_DEPTH,
        )
        if self.page_size > self.max_identities_per_run:
            raise ValueError("page_size cannot exceed max_identities_per_run.")

    @property
    def digest(self) -> str:
        text = ":".join(
            str(value)
            for value in (
                self.max_identities_per_run,
                self.max_directory_listings,
                self.max_entries_per_directory,
                self.page_size,
                self.max_relative_path_utf8_bytes,
                self.max_traversal_depth,
            )
        )
        return hashlib.sha256(text.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class CacheCatalogReconciliationCursor:
    mode: CacheCatalogReconciliationMode
    last_namespace: CacheNamespace
    last_entry_digest: str
    policy_digest: str
    final_complete: bool = False
    staging_complete: bool = False
    catalog_complete: bool = False
    cursor_version: int = RECONCILIATION_CURSOR_VERSION

    def __post_init__(self) -> None:
        if self.cursor_version != RECONCILIATION_CURSOR_VERSION:
            raise ValueError("Unsupported reconciliation cursor version.")
        if not isinstance(self.mode, CacheCatalogReconciliationMode):
            raise TypeError("mode must be CacheCatalogReconciliationMode.")
        if not isinstance(self.last_namespace, CacheNamespace):
            raise TypeError("last_namespace must be CacheNamespace.")
        if not isinstance(self.last_entry_digest, str) or _DIGEST.fullmatch(
            self.last_entry_digest
        ) is None:
            raise ValueError("last_entry_digest must be 64 lowercase hex.")
        if not isinstance(self.policy_digest, str) or _DIGEST.fullmatch(
            self.policy_digest
        ) is None:
            raise ValueError("policy_digest must be 64 lowercase hex.")
        if any(
            not isinstance(value, bool)
            for value in (self.final_complete, self.staging_complete, self.catalog_complete)
        ):
            raise TypeError("cursor completion flags must be boolean.")

    @property
    def sort_key(self) -> tuple[str, str, int, str]:
        return _identity_key(self.last_namespace, self.last_entry_digest)


@dataclass(frozen=True)
class DiscoveredCacheIdentity:
    identity: CacheCatalogIdentity
    sources: ReconciliationSourceFlags

    def __post_init__(self) -> None:
        if not isinstance(self.identity, CacheCatalogIdentity):
            raise TypeError("identity must be CacheCatalogIdentity.")
        if not isinstance(self.sources, ReconciliationSourceFlags) or not self.sources:
            raise ValueError("sources must contain at least one discovery source.")

    @property
    def sort_key(self) -> tuple[str, str, int, str]:
        return self.identity.sort_key


@dataclass(frozen=True)
class DiscoveredCatalogSlot:
    namespace: CacheNamespace
    entry_digest: str
    classification: CatalogSlotClassification
    record: CacheCatalogRecord | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, CacheNamespace):
            raise TypeError("namespace must be CacheNamespace.")
        if not isinstance(self.entry_digest, str) or _DIGEST.fullmatch(
            self.entry_digest
        ) is None:
            raise ValueError("entry_digest must be 64 lowercase hex.")
        if not isinstance(self.classification, CatalogSlotClassification):
            raise TypeError("classification must be CatalogSlotClassification.")
        supported = self.classification in {
            CatalogSlotClassification.SUPPORTED_LIVE,
            CatalogSlotClassification.SUPPORTED_TOMBSTONE,
        }
        if supported != isinstance(self.record, CacheCatalogRecord):
            raise ValueError("only a supported catalog slot carries a parsed record.")
        if self.record is not None and (
            self.record.identity.namespace != self.namespace
            or self.record.identity.entry_digest != self.entry_digest
        ):
            raise ValueError("catalog slot and parsed record identity disagree.")

    @property
    def trusted_identity(self) -> CacheCatalogIdentity | None:
        return None if self.record is None else self.record.identity

    @property
    def sort_key(self) -> tuple[str, str, int, str]:
        return _identity_key(self.namespace, self.entry_digest)


@dataclass(frozen=True)
class ReconciliationDiscoveryPage:
    identities: tuple[DiscoveredCacheIdentity, ...]
    next_cursor: CacheCatalogReconciliationCursor | None = None
    catalog_slots: tuple[DiscoveredCatalogSlot, ...] = ()
    is_snapshot: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.identities, tuple) or len(self.identities) > 256:
            raise ValueError("identities must be a bounded tuple.")
        if tuple(item.sort_key for item in self.identities) != tuple(
            sorted(item.sort_key for item in self.identities)
        ):
            raise ValueError("identities must use canonical order.")
        if not isinstance(self.catalog_slots, tuple) or len(self.catalog_slots) > 256:
            raise ValueError("catalog_slots must be a bounded tuple.")
        if self.next_cursor is not None and not isinstance(
            self.next_cursor, CacheCatalogReconciliationCursor
        ):
            raise TypeError("next_cursor must be a reconciliation cursor or None.")
        if self.is_snapshot is not False:
            raise ValueError("H2A pages never claim snapshot semantics.")


@dataclass(frozen=True)
class ReconciliationDirectoryListing:
    names: tuple[str, ...]
    limit_exceeded: bool
    pre_identity: FileIdentity
    post_identity: FileIdentity


@runtime_checkable
class ReconciliationReadOnlyFilesystem(Protocol):
    @property
    def cache_root(self) -> ValidatedCacheRoot: ...

    def inspect_root(self) -> FileIdentity: ...

    def inspect_relative(self, relative: Path) -> FileIdentity: ...

    def list_relative_bounded(
        self, relative: Path, *, max_entries: int
    ) -> ReconciliationDirectoryListing: ...

    def read_relative_bounded(
        self, relative: Path, *, max_bytes: int
    ) -> BoundedFileRead: ...


@dataclass(frozen=True)
class LocalReconciliationReadOnlyFilesystem:
    cache_root: ValidatedCacheRoot
    _filesystem: LocalReadOnlyCacheFilesystem = LocalReadOnlyCacheFilesystem()

    @classmethod
    def from_root(cls, path: str | Path) -> "LocalReconciliationReadOnlyFilesystem":
        filesystem = LocalReadOnlyCacheFilesystem()
        return cls(ValidatedCacheRoot.from_path(path, filesystem=filesystem), filesystem)

    def __post_init__(self) -> None:
        if not isinstance(self.cache_root, ValidatedCacheRoot):
            raise TypeError("cache_root must be ValidatedCacheRoot.")

    def _path(self, relative: Path) -> Path:
        _validate_relative(relative, ReconciliationDiscoveryPolicy())
        return self.cache_root.resolved_path / relative

    def inspect_root(self) -> FileIdentity:
        return self._filesystem.inspect(self.cache_root.resolved_path)

    def inspect_relative(self, relative: Path) -> FileIdentity:
        return self._filesystem.inspect(self._path(relative))

    def list_relative_bounded(
        self, relative: Path, *, max_entries: int
    ) -> ReconciliationDirectoryListing:
        limit = _bounded_positive(
            max_entries, "max_entries", MAX_RECONCILIATION_DIRECTORY_ENTRIES
        )
        path = self._path(relative)
        pre = self._filesystem.inspect(path)
        if pre.object_type is FilesystemObjectType.SYMLINK:
            raise SymlinkRejectedError("Reconciliation listing rejects symlinks.")
        if pre.object_type is not FilesystemObjectType.DIRECTORY:
            raise UnsupportedFilesystemObjectError(
                "Reconciliation listing requires a directory."
            )
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(path, flags)
            handle = FileIdentity.from_stat(os.fstat(descriptor))
            if not pre.same_stable_object(handle):
                raise UnstableFilesystemObjectError("Directory changed before listing.")
            names: list[str] = []
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    names.append(entry.name)
                    if len(names) == limit + 1:
                        break
            handle_after = FileIdentity.from_stat(os.fstat(descriptor))
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise UnsupportedFilesystemObjectError(
                    "Reconciliation directory is unsafe."
                ) from exc
            raise CacheLookupFilesystemError("Reconciliation listing failed.") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        post = self._filesystem.inspect(path)
        if not (
            pre.same_stable_object(handle)
            and handle.same_stable_object(handle_after)
            and handle_after.same_stable_object(post)
        ):
            raise UnstableFilesystemObjectError("Directory changed while listed.")
        return ReconciliationDirectoryListing(
            tuple(sorted(names)), len(names) > limit, pre, post
        )

    def read_relative_bounded(
        self, relative: Path, *, max_bytes: int
    ) -> BoundedFileRead:
        return self._filesystem.read_regular_file_bounded(
            self._path(relative), max_bytes=max_bytes
        )


_FORBIDDEN_SURFACE = frozenset(
    {
        "write",
        "create",
        "mkdir",
        "rename",
        "replace",
        "unlink",
        "chmod",
        "fsync",
        "flock",
        "acquire_lock",
        "release_lock",
        "promote",
        "cleanup",
    }
)


@dataclass
class _DiscoveryBudget:
    policy: ReconciliationDiscoveryPolicy
    listings: int = 0

    def consume_listing(self) -> None:
        self.listings += 1
        if self.listings > self.policy.max_directory_listings:
            raise ReconciliationDiscoveryLimitError(
                "Directory-listing budget exhausted."
            )


def _identity_key(
    namespace: CacheNamespace, entry_digest: str
) -> tuple[str, str, int, str]:
    return (
        namespace.domain,
        namespace.producer_id,
        namespace.producer_schema_version,
        entry_digest,
    )


def _validate_relative(relative: Path, policy: ReconciliationDiscoveryPolicy) -> None:
    if not isinstance(relative, Path) or relative.is_absolute() or not relative.parts:
        raise ReconciliationDiscoveryError("Discovery path must be contract-relative.")
    if any(part in {"", ".", ".."} or "/" in part or "\\" in part for part in relative.parts):
        raise ReconciliationDiscoveryError("Discovery path is noncanonical.")
    if len(relative.parts) > policy.max_traversal_depth:
        raise ReconciliationDiscoveryLimitError("Discovery path exceeds depth limit.")
    if len(relative.as_posix().encode("utf-8")) > policy.max_relative_path_utf8_bytes:
        raise ReconciliationDiscoveryLimitError("Discovery path exceeds byte limit.")


def _validate_filesystem(filesystem: ReconciliationReadOnlyFilesystem) -> None:
    if not isinstance(filesystem, ReconciliationReadOnlyFilesystem):
        raise TypeError("filesystem must implement ReconciliationReadOnlyFilesystem.")
    leaked = sorted(name for name in _FORBIDDEN_SURFACE if hasattr(filesystem, name))
    if leaked:
        raise TypeError("H2A rejects mutation-capable filesystem surfaces.")


def _require_root(filesystem: ReconciliationReadOnlyFilesystem) -> None:
    observed = filesystem.inspect_root()
    if not filesystem.cache_root.identity.same_stable_object(observed):
        raise ReconciliationDiscoveryError("Validated cache root changed.")
    if observed.object_type is not FilesystemObjectType.DIRECTORY:
        raise ReconciliationDiscoveryError("Validated cache root is not a directory.")


def _inspect_directory(
    filesystem: ReconciliationReadOnlyFilesystem,
    relative: Path,
    policy: ReconciliationDiscoveryPolicy,
) -> FileIdentity:
    _validate_relative(relative, policy)
    _require_root(filesystem)
    observed = filesystem.inspect_relative(relative)
    if observed.object_type is FilesystemObjectType.SYMLINK:
        raise ReconciliationDiscoveryError("Discovery encountered a symlink.")
    if observed.object_type is not FilesystemObjectType.DIRECTORY:
        raise ReconciliationDiscoveryError("Discovery expected a directory.")
    return observed


def _list_directory(
    filesystem: ReconciliationReadOnlyFilesystem,
    relative: Path,
    policy: ReconciliationDiscoveryPolicy,
    budget: _DiscoveryBudget,
) -> tuple[tuple[str, ...], FileIdentity]:
    before = _inspect_directory(filesystem, relative, policy)
    budget.consume_listing()
    listing = filesystem.list_relative_bounded(
        relative, max_entries=policy.max_entries_per_directory
    )
    if listing.limit_exceeded:
        raise ReconciliationDiscoveryLimitError("Directory exceeds entry limit.")
    if not (
        before.same_stable_object(listing.pre_identity)
        and listing.pre_identity.same_stable_object(listing.post_identity)
    ):
        raise ReconciliationDiscoveryError("Directory changed during discovery.")
    return listing.names, listing.post_identity


def _namespace(domain: str, producer: str, schema_name: str) -> CacheNamespace | None:
    try:
        schema = int(schema_name, 10)
        if schema <= 0 or str(schema) != schema_name:
            return None
        return CacheNamespace(domain, producer, schema)
    except (CacheEntryContractError, TypeError, ValueError):
        return None


def _revalidate_ancestors(
    filesystem: ReconciliationReadOnlyFilesystem,
    ancestors: tuple[tuple[Path, FileIdentity], ...],
) -> None:
    _require_root(filesystem)
    for relative, expected in ancestors:
        current = filesystem.inspect_relative(relative)
        if not expected.same_stable_object(current):
            raise ReconciliationDiscoveryError("Discovery ancestor changed.")


def _probe_identity(
    filesystem: ReconciliationReadOnlyFilesystem,
    candidate: Path,
    namespace: CacheNamespace,
    digest: str,
    ancestors: tuple[tuple[Path, FileIdentity], ...],
    policy: ReconciliationDiscoveryPolicy,
) -> CacheCatalogIdentity | None:
    candidate_identity = _inspect_directory(filesystem, candidate, policy)
    metadata_relative = candidate / "metadata.json"
    _validate_relative(metadata_relative, policy)
    try:
        read = filesystem.read_relative_bounded(
            metadata_relative, max_bytes=DEFAULT_MAX_METADATA_BYTES
        )
    except FileNotFoundError:
        return None
    except (CacheLookupFilesystemError, SymlinkRejectedError, UnsupportedFilesystemObjectError):
        raise ReconciliationDiscoveryError("Identity metadata is unsafe or unreadable.")
    if read.limit_exceeded or read.data is None:
        return None
    if not read.stable_read:
        raise ReconciliationDiscoveryError("Identity metadata changed while read.")
    try:
        metadata = CacheEntryMetadata.from_json(read.data)
    except CacheEntryContractError:
        return None
    if (
        metadata.namespace != namespace
        or metadata.entry_digest != digest
    ):
        return None
    identity = CacheCatalogIdentity(namespace, digest, metadata.cache_key)
    current_candidate = filesystem.inspect_relative(candidate)
    if not candidate_identity.same_stable_object(current_candidate):
        raise ReconciliationDiscoveryError("Candidate changed during identity probe.")
    _revalidate_ancestors(filesystem, ancestors)
    return identity


def _source_root(
    filesystem: ReconciliationReadOnlyFilesystem,
    relative: Path,
    policy: ReconciliationDiscoveryPolicy,
    budget: _DiscoveryBudget,
) -> tuple[tuple[str, ...], FileIdentity] | None:
    try:
        return _list_directory(filesystem, relative, policy, budget)
    except FileNotFoundError:
        return None


def _iter_namespaces(
    filesystem: ReconciliationReadOnlyFilesystem,
    root: Path,
    policy: ReconciliationDiscoveryPolicy,
    budget: _DiscoveryBudget,
) -> Iterator[tuple[CacheNamespace, Path, tuple[tuple[Path, FileIdentity], ...]]]:
    root_listing = _source_root(filesystem, root, policy, budget)
    if root_listing is None:
        return
    domains, root_identity = root_listing
    root_ancestors = ((root, root_identity),)
    for domain in domains:
        domain_path = root / domain
        try:
            CacheNamespace(domain, "reconciliation.validation", 1)
        except (CacheEntryContractError, TypeError, ValueError):
            continue
        producers, domain_identity = _list_directory(
            filesystem, domain_path, policy, budget
        )
        for producer in producers:
            producer_path = domain_path / producer
            try:
                CacheNamespace(domain, producer, 1)
            except (CacheEntryContractError, TypeError, ValueError):
                continue
            schemas, producer_identity = _list_directory(
                filesystem, producer_path, policy, budget
            )
            parsed: list[tuple[int, str, CacheNamespace]] = []
            for schema_name in schemas:
                namespace = _namespace(domain, producer, schema_name)
                if namespace is not None:
                    parsed.append((namespace.producer_schema_version, schema_name, namespace))
            for _, schema_name, namespace in sorted(parsed):
                schema_path = producer_path / schema_name
                schema_identity = _inspect_directory(filesystem, schema_path, policy)
                yield namespace, schema_path, (
                    *root_ancestors,
                    (domain_path, domain_identity),
                    (producer_path, producer_identity),
                    (schema_path, schema_identity),
                )


def iter_final_discovered_identities(
    filesystem: ReconciliationReadOnlyFilesystem,
    *,
    policy: ReconciliationDiscoveryPolicy = ReconciliationDiscoveryPolicy(),
    _budget: _DiscoveryBudget | None = None,
) -> Iterator[DiscoveredCacheIdentity]:
    _validate_filesystem(filesystem)
    budget = _budget or _DiscoveryBudget(policy)
    for namespace, schema_path, ancestors in _iter_namespaces(
        filesystem, Path("entries", "v1"), policy, budget
    ):
        first_names, schema_identity = _list_directory(
            filesystem, schema_path, policy, budget
        )
        for first in first_names:
            if _SHARD.fullmatch(first) is None:
                continue
            first_path = schema_path / first
            second_names, first_identity = _list_directory(
                filesystem, first_path, policy, budget
            )
            for second in second_names:
                if _SHARD.fullmatch(second) is None:
                    continue
                second_path = first_path / second
                digests, second_identity = _list_directory(
                    filesystem, second_path, policy, budget
                )
                for digest in digests:
                    if (
                        _DIGEST.fullmatch(digest) is None
                        or digest[:2] != first
                        or digest[2:4] != second
                    ):
                        continue
                    candidate = second_path / digest
                    identity = _probe_identity(
                        filesystem,
                        candidate,
                        namespace,
                        digest,
                        (
                            *ancestors[:-1],
                            (schema_path, schema_identity),
                            (first_path, first_identity),
                            (second_path, second_identity),
                        ),
                        policy,
                    )
                    if identity is not None:
                        yield DiscoveredCacheIdentity(
                            identity, ReconciliationSourceFlags.FINAL
                        )


def iter_staging_discovered_identities(
    filesystem: ReconciliationReadOnlyFilesystem,
    *,
    policy: ReconciliationDiscoveryPolicy = ReconciliationDiscoveryPolicy(),
    _budget: _DiscoveryBudget | None = None,
) -> Iterator[DiscoveredCacheIdentity]:
    _validate_filesystem(filesystem)
    budget = _budget or _DiscoveryBudget(policy)
    previous: CacheCatalogIdentity | None = None
    for namespace, schema_path, ancestors in _iter_namespaces(
        filesystem, Path("staging", "v1"), policy, budget
    ):
        names, schema_identity = _list_directory(filesystem, schema_path, policy, budget)
        for name in names:
            digest, separator, token = name.partition(".")
            if (
                not separator
                or _DIGEST.fullmatch(digest) is None
                or _WRITER_TOKEN.fullmatch(token) is None
                or ".." in token
            ):
                continue
            candidate = schema_path / name
            identity = _probe_identity(
                filesystem,
                candidate,
                namespace,
                digest,
                (*ancestors[:-1], (schema_path, schema_identity)),
                policy,
            )
            if identity is not None and identity != previous:
                yield DiscoveredCacheIdentity(identity, ReconciliationSourceFlags.STAGING)
                previous = identity


def _catalog_list(
    backend: CacheCatalogReadOnlyBackend,
    relative: Path,
    policy: ReconciliationDiscoveryPolicy,
    budget: _DiscoveryBudget,
) -> tuple[str, ...]:
    _validate_relative(relative, policy)
    budget.consume_listing()
    listing = backend.list_catalog_relative(relative)
    if listing.limit_exceeded or len(listing.names) > policy.max_entries_per_directory:
        raise ReconciliationDiscoveryLimitError("Catalog directory exceeds entry limit.")
    if not listing.pre_identity.same_stable_object(listing.post_identity):
        raise ReconciliationDiscoveryError("Catalog directory changed while listed.")
    return listing.names


def _validate_catalog_backend(backend: CacheCatalogReadOnlyBackend) -> None:
    if not isinstance(backend, CacheCatalogReadOnlyBackend):
        raise TypeError("backend must implement CacheCatalogReadOnlyBackend.")
    forbidden = {
        "initialize_catalog",
        "acquire_writer_lock",
        "ensure_record_parent",
        "publish_record_bytes",
    }
    if any(hasattr(backend, name) for name in forbidden):
        raise TypeError("H2A rejects mutation-capable catalog backends.")


def _catalog_record_slot(
    backend: CacheCatalogReadOnlyBackend,
    namespace: CacheNamespace,
    digest: str,
) -> DiscoveredCatalogSlot:
    try:
        read = backend.read_discovered_record(namespace, digest)
    except FileNotFoundError as exc:
        raise ReconciliationDiscoveryError("Catalog slot disappeared.") from exc
    except (SymlinkRejectedError, UnsupportedFilesystemObjectError) as exc:
        raise ReconciliationDiscoveryError("Catalog slot is unsafe.") from exc
    except UnstableFilesystemObjectError as exc:
        raise ReconciliationDiscoveryError("Catalog slot is unstable.") from exc
    except (CacheLookupFilesystemError, OSError) as exc:
        raise ReconciliationDiscoveryError("Catalog slot read failed.") from exc
    if not read.stable_read:
        raise ReconciliationDiscoveryError("Catalog slot changed while read.")
    if read.limit_exceeded or read.data is None:
        return DiscoveredCatalogSlot(
            namespace, digest, CatalogSlotClassification.CORRUPT
        )
    try:
        record = parse_cache_catalog_record(read.data)
    except CacheCatalogUnsupportedVersionError:
        return DiscoveredCatalogSlot(
            namespace, digest, CatalogSlotClassification.UNSUPPORTED
        )
    except CacheCatalogContractError:
        return DiscoveredCatalogSlot(
            namespace, digest, CatalogSlotClassification.CORRUPT
        )
    if record.identity.namespace != namespace or record.identity.entry_digest != digest:
        return DiscoveredCatalogSlot(
            namespace, digest, CatalogSlotClassification.CORRUPT
        )
    classification = (
        CatalogSlotClassification.SUPPORTED_LIVE
        if isinstance(record, CacheCatalogLiveRecord)
        else CatalogSlotClassification.SUPPORTED_TOMBSTONE
    )
    return DiscoveredCatalogSlot(namespace, digest, classification, record)


def iter_catalog_slots(
    backend: CacheCatalogReadOnlyBackend,
    *,
    policy: ReconciliationDiscoveryPolicy = ReconciliationDiscoveryPolicy(),
    _budget: _DiscoveryBudget | None = None,
) -> Iterator[DiscoveredCatalogSlot]:
    _validate_catalog_backend(backend)
    budget = _budget or _DiscoveryBudget(policy)
    root = Path("catalog", f"v{CACHE_CATALOG_LAYOUT_VERSION}", "records")
    try:
        domains = _catalog_list(backend, root, policy, budget)
    except FileNotFoundError:
        return
    for domain in domains:
        try:
            CacheNamespace(domain, "reconciliation.validation", 1)
        except (CacheEntryContractError, TypeError, ValueError):
            continue
        domain_path = root / domain
        for producer in _catalog_list(backend, domain_path, policy, budget):
            try:
                CacheNamespace(domain, producer, 1)
            except (CacheEntryContractError, TypeError, ValueError):
                continue
            producer_path = domain_path / producer
            schemas = _catalog_list(backend, producer_path, policy, budget)
            parsed_schemas: list[tuple[int, str, CacheNamespace]] = []
            for schema_name in schemas:
                namespace = _namespace(domain, producer, schema_name)
                if namespace is not None:
                    parsed_schemas.append(
                        (namespace.producer_schema_version, schema_name, namespace)
                    )
            for _, schema_name, namespace in sorted(parsed_schemas):
                schema_path = producer_path / schema_name
                for first in _catalog_list(backend, schema_path, policy, budget):
                    if _SHARD.fullmatch(first) is None:
                        continue
                    first_path = schema_path / first
                    for second in _catalog_list(backend, first_path, policy, budget):
                        if _SHARD.fullmatch(second) is None:
                            continue
                        second_path = first_path / second
                        for name in _catalog_list(backend, second_path, policy, budget):
                            if _CATALOG_TEMP.fullmatch(name) is not None:
                                continue
                            match = _CATALOG_RECORD.fullmatch(name)
                            if match is None:
                                continue
                            digest = match.group(1)
                            if digest[:2] != first or digest[2:4] != second:
                                continue
                            yield _catalog_record_slot(backend, namespace, digest)


def _iter_catalog_identities(
    backend: CacheCatalogReadOnlyBackend,
    policy: ReconciliationDiscoveryPolicy,
    budget: _DiscoveryBudget,
) -> Iterator[DiscoveredCacheIdentity]:
    for slot in iter_catalog_slots(backend, policy=policy, _budget=budget):
        if slot.trusted_identity is not None:
            yield DiscoveredCacheIdentity(
                slot.trusted_identity, ReconciliationSourceFlags.CATALOG
            )


def _merged_identities(
    streams: tuple[Iterator[DiscoveredCacheIdentity], ...]
) -> Iterator[DiscoveredCacheIdentity]:
    current: list[DiscoveredCacheIdentity | None] = []
    for stream in streams:
        current.append(next(stream, None))
    while any(item is not None for item in current):
        key = min(item.sort_key for item in current if item is not None)
        identity: CacheCatalogIdentity | None = None
        flags = ReconciliationSourceFlags(0)
        for index, item in enumerate(current):
            while item is not None and item.sort_key == key:
                identity = item.identity
                flags |= item.sources
                item = next(streams[index], None)
            current[index] = item
        assert identity is not None
        yield DiscoveredCacheIdentity(identity, flags)


def discover_reconciliation_identities(
    filesystem: ReconciliationReadOnlyFilesystem,
    *,
    catalog_backend: CacheCatalogReadOnlyBackend,
    policy: ReconciliationDiscoveryPolicy = ReconciliationDiscoveryPolicy(),
    cursor: CacheCatalogReconciliationCursor | None = None,
) -> ReconciliationDiscoveryPage:
    """Return one bounded H2A page; perform no validation or mutation beyond discovery."""

    _validate_filesystem(filesystem)
    _validate_catalog_backend(catalog_backend)
    if not isinstance(policy, ReconciliationDiscoveryPolicy):
        raise TypeError("policy must be ReconciliationDiscoveryPolicy.")
    if cursor is not None:
        if not isinstance(cursor, CacheCatalogReconciliationCursor):
            raise TypeError("cursor must be CacheCatalogReconciliationCursor or None.")
        if cursor.mode is not CacheCatalogReconciliationMode.FULL_IN_PLACE:
            raise ValueError("cursor mode does not match full discovery.")
        if cursor.policy_digest != policy.digest:
            raise ValueError("cursor policy does not match discovery policy.")
    if not filesystem.cache_root.identity.same_stable_object(
        catalog_backend.cache_root.identity
    ):
        raise ValueError("filesystem and catalog backend must share one validated root.")
    budget = _DiscoveryBudget(policy)
    merged = _merged_identities(
        (
            iter_final_discovered_identities(filesystem, policy=policy, _budget=budget),
            iter_staging_discovered_identities(filesystem, policy=policy, _budget=budget),
            _iter_catalog_identities(catalog_backend, policy, budget),
        )
    )
    last_key = None if cursor is None else cursor.sort_key
    page: list[DiscoveredCacheIdentity] = []
    for item in merged:
        if last_key is not None and item.sort_key <= last_key:
            continue
        page.append(item)
        if len(page) > policy.page_size:
            break
    if len(page) <= policy.page_size:
        return ReconciliationDiscoveryPage(tuple(page))
    emitted = tuple(page[: policy.page_size])
    last = emitted[-1].identity
    return ReconciliationDiscoveryPage(
        emitted,
        CacheCatalogReconciliationCursor(
            CacheCatalogReconciliationMode.FULL_IN_PLACE,
            last.namespace,
            last.entry_digest,
            policy.digest,
        ),
    )


def discover_catalog_slots_page(
    backend: CacheCatalogReadOnlyBackend,
    *,
    policy: ReconciliationDiscoveryPolicy = ReconciliationDiscoveryPolicy(),
    cursor: CacheCatalogReconciliationCursor | None = None,
) -> ReconciliationDiscoveryPage:
    """Return structural catalog slots, including safely classified corrupt slots."""

    if cursor is not None and (
        cursor.mode is not CacheCatalogReconciliationMode.FULL_IN_PLACE
        or cursor.policy_digest != policy.digest
    ):
        raise ValueError("cursor does not match catalog slot discovery.")
    last_key = None if cursor is None else cursor.sort_key
    slots: list[DiscoveredCatalogSlot] = []
    for slot in iter_catalog_slots(backend, policy=policy):
        if last_key is not None and slot.sort_key <= last_key:
            continue
        slots.append(slot)
        if len(slots) > policy.page_size:
            break
    if len(slots) <= policy.page_size:
        return ReconciliationDiscoveryPage((), catalog_slots=tuple(slots))
    emitted = tuple(slots[: policy.page_size])
    last = emitted[-1]
    return ReconciliationDiscoveryPage(
        (),
        CacheCatalogReconciliationCursor(
            CacheCatalogReconciliationMode.FULL_IN_PLACE,
            last.namespace,
            last.entry_digest,
            policy.digest,
        ),
        emitted,
    )
