"""Read-only access to harness data, and the guard that proves it stayed read-only."""

from agdash.access.guard import HarnessDataChanged, assert_untouched, snapshot_tree
from agdash.access.readonly import (
    AccessError,
    NotARegularFile,
    OutsideAllowedRoots,
    ReadOnlyAccess,
    SnapshotTooLarge,
    SnapshotUnstable,
    WriteRefused,
)

__all__ = [
    "AccessError",
    "HarnessDataChanged",
    "NotARegularFile",
    "OutsideAllowedRoots",
    "ReadOnlyAccess",
    "SnapshotTooLarge",
    "SnapshotUnstable",
    "WriteRefused",
    "assert_untouched",
    "snapshot_tree",
]
