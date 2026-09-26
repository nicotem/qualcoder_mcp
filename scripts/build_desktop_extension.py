#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Build the Claude Desktop extension (.mcpb) from a release of this
repository.

    python scripts/build_desktop_extension.py [--ref REF] [--out DIR]
    python scripts/build_desktop_extension.py --worktree [--out DIR]

With --ref (default HEAD) every file is read from git at that commit,
never from the working tree, so a tag such as v0.14.0-alpha always gives
the same bytes: the entries are written in sorted order, uncompressed
(the bytes then depend on no compression library), with fixed
permissions and the commit's own date. --worktree reads the files on
disk instead, for trying uncommitted changes and for the test suite; its
entries carry SOURCE_DATE_EPOCH, or 1980-01-01 when that is not set.

What the package holds: the release's pyproject.toml, uv.lock, README.md
(the readme pyproject names), the licence files pyproject names, the
package under src/qualcoder_mcp, a .python-version asking uv for Python
3.13 (a version CI tests), and manifest.json. The manifest is
packaging/desktop-extension/manifest.in.json with the fields that are
typed once elsewhere filled in: the name, version, author, licence,
keywords, links and Python requirement from pyproject.toml, and the
tools from the server itself, started from the same files with the
`lifecycle` tool set (the widest: `full` and `core` are subsets).

It writes DIR/qualcoder-mcp-<version>.mcpb and the same files unpacked
in DIR/qualcoder-mcp-<version>/, and prints the package's SHA-256.
Listing the tools needs the `mcp` library in this interpreter
(`pip install -e .` in a clone gives it). Signing is not done here.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10, where pytest brings tomli
    import tomli as tomllib

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = "packaging/desktop-extension/manifest.in.json"
PACKAGE = "src/qualcoder_mcp"
# The Python the package asks uv for. Without it uv takes the newest
# Python it can find or fetch, which may be one no CI job has tested; a
# test pins this to a version in ci.yml's matrix.
PYTHON_VERSION = "3.13"
# The fields the build fills in, which the template must not hold: each
# is typed once, in pyproject.toml or in the server's code.
GENERATED = ("name", "version", "author", "repository", "homepage",
             "support", "keywords", "license", "tools")
# The tool set whose tools the manifest lists: `lifecycle` is `full`
# plus create_project, and `core` is a subset of `full`.
LISTED_TOOLSET = "lifecycle"
EARLIEST_ZIP_TIME = 315532800          # 1980-01-01T00:00:00Z
SUMMARY_LIMIT = 240


class BuildError(Exception):
    """A refusal to build, in words for whoever ran the script."""


# ---------------------------------------------------------------------------
# Where the files come from
# ---------------------------------------------------------------------------

def _git(*args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=str(REPO),
                            capture_output=True)
    if result.returncode != 0:
        raise BuildError(f"git {' '.join(args)} failed: "
                         f"{result.stderr.decode(errors='replace').strip()}")
    return result.stdout


class GitSource:
    """The files of one commit, read from git."""

    def __init__(self, ref: str):
        self.ref = ref
        self.commit = _git("rev-parse", "--verify",
                           f"{ref}^{{commit}}").decode().strip()
        self.epoch = int(_git("show", "-s", "--format=%ct",
                              self.commit).decode().strip())

    def read(self, path: str) -> bytes:
        return _git("show", f"{self.commit}:{path}")

    def files_under(self, folder: str) -> List[str]:
        names = _git("ls-tree", "-r", "--name-only", self.commit, "--",
                     folder).decode().splitlines()
        return sorted(n for n in names if n)

    def describe(self) -> str:
        return f"{self.ref} ({self.commit[:12]})"


class TreeSource:
    """The files on disk, for trying changes before they are committed."""

    def __init__(self, root: Path = REPO):
        self.root = root
        self.epoch = int(os.environ.get("SOURCE_DATE_EPOCH",
                                        EARLIEST_ZIP_TIME))

    def read(self, path: str) -> bytes:
        return (self.root / path).read_bytes()

    def files_under(self, folder: str) -> List[str]:
        base = self.root / folder
        return sorted(p.relative_to(self.root).as_posix()
                      for p in base.rglob("*")
                      if p.is_file() and "__pycache__" not in p.parts
                      and p.suffix not in (".pyc", ".pyo"))

    def describe(self) -> str:
        return f"the working tree at {self.root}"


def load_pyproject(source) -> dict:
    return tomllib.loads(source.read("pyproject.toml").decode("utf-8"))


def package_files(source, project: dict) -> Dict[str, bytes]:
    """Every file the package holds apart from manifest.json and
    .python-version, by its path in the package."""
    names = ["pyproject.toml", "uv.lock", project["project"]["readme"]]
    names += list(project["project"].get("license-files", []))
    names += source.files_under(PACKAGE)
    if not any(n.endswith("/server.py") for n in names):
        raise BuildError(f"no server module under {PACKAGE} in "
                         f"{source.describe()}")
    return {name: source.read(name) for name in names}


# ---------------------------------------------------------------------------
# The tools, from the server itself
# ---------------------------------------------------------------------------

# Run in a child interpreter: put the package's own src first, refuse to
# go on if another copy of qualcoder_mcp answers the import, and start
# the server as a host does.
_LAUNCH = (
    "import sys\n"
    "from pathlib import Path\n"
    "src = Path(sys.argv[1]).resolve()\n"
    "sys.path.insert(0, str(src))\n"
    "import qualcoder_mcp\n"
    "if not Path(qualcoder_mcp.__file__).resolve().is_relative_to(src):\n"
    "    sys.exit('another qualcoder_mcp was imported: '\n"
    "             + qualcoder_mcp.__file__)\n"
    "from qualcoder_mcp.server import main\n"
    "main([])\n"
)


def list_tools(files: Dict[str, bytes],
               toolset: str = LISTED_TOOLSET) -> List[Tuple[str, str]]:
    """(name, description) of every tool the server registers with
    `toolset`, in the server's order, asked over stdio as a host asks.

    The server runs from `files` written to a scratch folder, with its
    home there too, so nothing of the builder's own is read or written.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    with tempfile.TemporaryDirectory(prefix="qcmcp-mcpb-") as scratch:
        scratch_path = Path(scratch)
        for name, data in files.items():
            target = scratch_path / "tree" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        home = scratch_path / "home"
        home.mkdir()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("QUALCODER")}
        env.update({"HOME": str(home), "USERPROFILE": str(home),
                    "QUALCODER_MCP_TOOLSET": toolset,
                    "PYTHONDONTWRITEBYTECODE": "1"})
        params = StdioServerParameters(
            command=sys.executable,
            args=["-B", "-c", _LAUNCH,
                  str(scratch_path / "tree" / "src")],
            env=env, cwd=str(scratch_path))

        log_path = scratch_path / "server.log"

        async def ask(log):
            async with stdio_client(params, errlog=log) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return (await session.list_tools()).tools

        with open(log_path, "w", encoding="utf-8") as log:
            try:
                tools = asyncio.run(ask(log))
            except Exception as error:
                log.flush()
                tail = log_path.read_text(encoding="utf-8",
                                          errors="replace")[-2000:]
                raise BuildError(f"the server did not list its tools "
                                 f"({type(error).__name__}); its output "
                                 f"ended:\n{tail}") from None
    return [(tool.name, tool.description or "") for tool in tools]


def summary(description: str) -> str:
    """The first sentence of a tool's description, on one line, for the
    extension's details page."""
    paragraph = description.strip().split("\n\n")[0]
    text = " ".join(paragraph.split())
    match = re.search(r"(?<=[.!?])\s+(?=[A-Z`\"(])", text)
    if match:
        text = text[:match.start()]
    if len(text) > SUMMARY_LIMIT:
        text = text[:SUMMARY_LIMIT - 3].rsplit(" ", 1)[0] + "..."
    return text


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------

def build_manifest(template: dict, project: dict,
                   tools: List[Tuple[str, str]]) -> dict:
    """The template with the generated fields filled in, in the order of
    the specification's example."""
    typed = sorted(set(GENERATED) & set(template))
    if typed:
        raise BuildError(f"{TEMPLATE} holds {', '.join(typed)}, which the "
                         f"build takes from pyproject.toml or the server; "
                         f"remove them there")
    meta = project["project"]
    urls = meta.get("urls", {})
    author = {"name": meta["authors"][0]["name"]}
    if urls.get("Homepage"):
        # The address, not the e-mail: README says the e-mail in the
        # package metadata is not a support channel, and `support` below
        # is the issues page.
        author["url"] = urls["Homepage"]
    runtimes = dict(template.get("compatibility", {}).get("runtimes", {}))
    runtimes["python"] = meta["requires-python"]
    compatibility = dict(template.get("compatibility", {}))
    compatibility["runtimes"] = runtimes
    manifest = {
        "manifest_version": template["manifest_version"],
        "name": meta["name"],
        "display_name": template.get("display_name", meta["name"]),
        "version": meta["version"],
        "description": template["description"],
        "long_description": template.get("long_description"),
        "author": author,
        "repository": ({"type": "git", "url": urls["Repository"]}
                       if urls.get("Repository") else None),
        "homepage": urls.get("Homepage"),
        "documentation": template.get("documentation"),
        "support": urls.get("Issues"),
        "server": template["server"],
        "tools": [{"name": n, "description": summary(d)} for n, d in tools],
        "tools_generated": template.get("tools_generated"),
        "prompts_generated": template.get("prompts_generated"),
        "keywords": list(meta.get("keywords", [])),
        "license": meta.get("license"),
        "compatibility": compatibility,
        "user_config": template.get("user_config"),
    }
    unknown = sorted(set(template) - set(manifest))
    if unknown:
        raise BuildError(f"{TEMPLATE} holds {', '.join(unknown)}, which "
                         f"this build does not place; teach build_manifest")
    return {k: v for k, v in manifest.items() if v is not None}


def manifest_bytes(manifest: dict) -> bytes:
    return (json.dumps(manifest, indent=2, ensure_ascii=False)
            + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# The package
# ---------------------------------------------------------------------------

def bundle(source) -> Tuple[dict, Dict[str, bytes]]:
    """(manifest, every file of the package by its path in it)."""
    project = load_pyproject(source)
    files = package_files(source, project)
    template = json.loads(source.read(TEMPLATE).decode("utf-8"))
    manifest = build_manifest(template, project, list_tools(files))
    files[".python-version"] = f"{PYTHON_VERSION}\n".encode("ascii")
    files["manifest.json"] = manifest_bytes(manifest)
    return manifest, files


def write_mcpb(files: Dict[str, bytes], target: Path, epoch: int) -> str:
    """Write the package and return its SHA-256.

    Sorted entries, stored rather than deflated, one fixed date, mode
    0644 and the Unix host byte, whatever the builder's platform: the
    same files give the same bytes. Claude Desktop reads the package as
    an ordinary ZIP; a signature, if one is ever added, is appended
    after it (the MCPB format's MCPB_SIG_V1 block).
    """
    stamp = time.gmtime(max(epoch, EARLIEST_ZIP_TIME))[:6]
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as z:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=stamp)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            z.writestr(info, files[name])
    return hashlib.sha256(target.read_bytes()).hexdigest()


def write_unpacked(files: Dict[str, bytes], folder: Path) -> None:
    """The same files as a folder, for the validator and for trying the
    package by hand. Only a folder this script made is replaced."""
    if folder.exists():
        mark = folder / "manifest.json"
        try:
            ours = json.loads(mark.read_text(encoding="utf-8"))["name"] \
                == "qualcoder-mcp"
        except (OSError, ValueError, KeyError, TypeError):
            ours = False
        if not ours:
            raise BuildError(f"{folder} exists and is not a package this "
                             f"script unpacked; choose another --out")
        shutil.rmtree(folder)
    for name, data in files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def build(source, out: Path) -> dict:
    """Build from `source` into `out`; what was built, for the caller."""
    manifest, files = bundle(source)
    stem = f"{manifest['name']}-{manifest['version']}"
    mcpb = out / f"{stem}.mcpb"
    digest = write_mcpb(files, mcpb, source.epoch)
    write_unpacked(files, out / stem)
    return {"source": source.describe(), "version": manifest["version"],
            "tools": len(manifest["tools"]), "files": len(files),
            "mcpb": mcpb, "unpacked": out / stem, "sha256": digest,
            "size": mcpb.stat().st_size}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the Claude Desktop extension (.mcpb).")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--ref", default="HEAD",
                       help="the commit or tag to build (default HEAD)")
    where.add_argument("--worktree", action="store_true",
                       help="build from the files on disk instead")
    parser.add_argument("--out", type=Path, default=REPO / "dist",
                        help="the folder to write into (default dist/)")
    args = parser.parse_args(argv)
    try:
        source = TreeSource() if args.worktree else GitSource(args.ref)
        result = build(source, args.out.resolve())
    except BuildError as error:
        print(f"build_desktop_extension: {error}", file=sys.stderr)
        return 1
    print(f"Built from {result['source']}: qualcoder-mcp "
          f"{result['version']}, {result['tools']} tools, "
          f"{result['files']} files")
    print(f"Package:  {result['mcpb']} ({result['size']:,} bytes)")
    print(f"Unpacked: {result['unpacked']}")
    print(f"SHA-256:  {result['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
