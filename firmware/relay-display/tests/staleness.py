"""The staleness check the emulator suite runs before it trusts a build.

The suite's conftest names its image, sources and rebuild command; the rule lives here.

mtime is the whole of the signal available. There is no build id in the image to
compare a commit against, and `project_description.json`'s `git describe` reads
`-dirty` for any working tree with an edit in it, which is most of the trees this
would need to judge.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def stale_reason(
    image: Path,
    project: Path,
    sources: Iterable[str],
    *,
    rebuild: str,
    override: str,
) -> str | None:
    """The first source newer than `image`, as one sentence, or None.

    `sources` are paths relative to `project`. A directory is searched recursively,
    and a path that leaves the project (`../components`) is resolved first, so a
    shared header edited after the build makes the build stale too.

    The sentence names the file, so a skip that reads "stale" is not the only thing a
    reader is given, and it names the command that fixes it and the variable that
    points the suite somewhere else.
    """
    built = image.stat().st_mtime
    for entry in sources:
        path = (project / entry).resolve()
        candidates = path.rglob("*") if path.is_dir() else iter((path,))
        for source in candidates:
            if source.is_file() and source.stat().st_mtime > built:
                return (
                    f"{source.relative_to(project.parent.parent)} is newer than "
                    f"{image}, so this suite would run against firmware that is not "
                    f"the firmware in this checkout. Rebuild it with `{rebuild}`, or "
                    f"point {override} at a build made elsewhere."
                )
    return None
