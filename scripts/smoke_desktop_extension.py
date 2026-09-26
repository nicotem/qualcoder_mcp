#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Install a built .mcpb the way Claude Desktop does, start it, and ask
it for its tools, without Claude Desktop.

    python scripts/smoke_desktop_extension.py PACKAGE.mcpb --into DIR
        [--uv PATH] [--set KEY=VALUE ...] [--expect-tools N|manifest]
        [--create NAME] [--offline-restart]

The steps are the ones Claude Desktop 2.9939.2 takes for an extension of
the `uv` type, read from its own code: unpack the package into a folder
named after the extension; run `uv sync --quiet` there; then start
`uv` with the manifest's `mcp_config` arguments and environment, the
folder as the working folder, after the app's own substitution of
`${__dirname}`, `${HOME}` and the other folders, and of
`${user_config.KEY}` (the setting's default unless --set gives another).
The substitution is one pass in the app's order, so a `${HOME}` inside a
setting's default is left as it is, as the app leaves it.

The caller chooses the home folder (set HOME, and USERPROFILE on
Windows, before running this); uv, Python and the packages are fetched
there as they would be on a tester's computer. It prints what it saw as
JSON and exits 1 when an expectation fails.
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def extension_id(manifest: dict) -> str:
    """The folder name Claude Desktop gives a package installed from a
    file: `local.mcpb.` then the author's and the extension's names,
    lower-cased, spaces to hyphens, other characters dropped."""
    def clean(text: str) -> str:
        text = re.sub(r"\s+", "-", text.lower())
        text = re.sub(r"[^a-z0-9-_.]", "", text)
        return re.sub(r"-+", "-", text).strip("-")
    return f"local.mcpb.{clean(manifest['author']['name'])}." \
           f"{clean(manifest['name'])}"


def substitute(value, variables):
    """The app's substitution (one pass, in the variables' order)."""
    if isinstance(value, str):
        for key, replacement in variables.items():
            value = value.replace("${" + key + "}", replacement)
        return value
    if isinstance(value, list):
        return [substitute(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, variables) for k, v in value.items()}
    return value


def launch_config(manifest: dict, folder: Path, settings: dict) -> dict:
    home = Path.home()
    variables = {"__dirname": str(folder), "pathSeparator": os.sep,
                 "/": os.sep, "HOME": str(home),
                 "DESKTOP": str(home / "Desktop"),
                 "DOCUMENTS": str(home / "Documents"),
                 "DOWNLOADS": str(home / "Downloads")}
    values = {k: v["default"] for k, v in
              manifest.get("user_config", {}).items() if "default" in v}
    values.update(settings)
    for key, value in values.items():
        variables[f"user_config.{key}"] = (
            ("true" if value else "false") if isinstance(value, bool)
            else str(value))
    return substitute(manifest["server"]["mcp_config"], variables)


async def ask(uv: str, config: dict, folder: Path, create: str = None):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env = dict(os.environ)
    env.update(config.get("env", {}))
    params = StdioServerParameters(command=uv, args=config["args"],
                                   env=env, cwd=str(folder))
    seen = {}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            started = await session.initialize()
            seen["server"] = started.serverInfo.name
            tools = (await session.list_tools()).tools
            seen["tools"] = len(tools)
            seen["tool_names"] = sorted(t.name for t in tools)

            async def call(name, arguments):
                result = await session.call_tool(name, arguments)
                text = "".join(getattr(b, "text", "") for b in
                               result.content)
                try:
                    return json.loads(text)
                except ValueError:
                    return text
            seen["current_project"] = await call("get_current_project", {})
            if create:
                seen["created"] = await call(
                    "create_project",
                    {"name": create, "coder_name_not_known": True})
                seen["listed"] = await call("list_available_projects", {})
    return seen


def install(package: Path, into: Path, uv: str) -> tuple:
    """Unpack and `uv sync --quiet`, as the app does; (manifest, folder)."""
    with zipfile.ZipFile(package) as z:
        manifest = json.loads(z.read("manifest.json"))
        folder = into / "Claude Extensions" / extension_id(manifest)
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        for name in z.namelist():
            target = (folder / name).resolve()
            if not target.is_relative_to(folder.resolve()):
                raise SystemExit(f"unsafe path in the package: {name}")
            z.extract(name, folder)
    subprocess.run([uv, "sync", "--quiet"], cwd=str(folder), check=True)
    return manifest, folder


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("package", type=Path)
    parser.add_argument("--into", type=Path, required=True,
                        help="the folder standing in for the app's data")
    parser.add_argument("--uv", default=shutil.which("uv") or "uv")
    parser.add_argument("--set", action="append", default=[],
                        metavar="KEY=VALUE", help="a setting's value")
    parser.add_argument("--expect-tools", metavar="N|manifest",
                        help="a number, or `manifest`: exactly the tools "
                             "the manifest lists")
    parser.add_argument("--create", metavar="NAME",
                        help="create a project with the default folder")
    parser.add_argument("--offline-restart", action="store_true",
                        help="start it a second time with uv offline")
    args = parser.parse_args(argv)
    settings = dict(item.split("=", 1) for item in args.set)

    manifest, folder = install(args.package.resolve(), args.into.resolve(),
                               args.uv)
    config = launch_config(manifest, folder, settings)
    seen = {"extension_folder": folder.name, "command_args": config["args"],
            "env": config.get("env", {}),
            "python_version_file": (folder / ".python-version")
            .read_text().strip()}
    seen.update(asyncio.run(ask(args.uv, config, folder, args.create)))
    failures = []
    if args.expect_tools == "manifest":
        listed = sorted(t["name"] for t in manifest.get("tools", []))
        if seen["tool_names"] != listed:
            failures.append(
                f"the server's tools are not the manifest's: missing "
                f"{sorted(set(listed) - set(seen['tool_names']))}, extra "
                f"{sorted(set(seen['tool_names']) - set(listed))}")
    elif args.expect_tools is not None and \
            seen["tools"] != int(args.expect_tools):
        failures.append(f"{seen['tools']} tools, expected "
                        f"{args.expect_tools}")
    if args.create:
        created = seen.get("created", {})
        if not isinstance(created, dict) or created.get("created") is not True:
            failures.append(f"create_project did not create: {created}")
        else:
            names = [p.get("name") for p in
                     seen.get("listed", {}).get("projects", [])]
            if args.create not in names:
                failures.append("the created project was not listed")
    if args.offline_restart:
        os.environ["UV_OFFLINE"] = "1"
        again = asyncio.run(ask(args.uv, config, folder))
        seen["offline_restart_tools"] = again["tools"]
        if again["tools"] != seen["tools"]:
            failures.append("the offline restart listed other tools")
    seen["failures"] = failures
    seen.pop("tool_names", None)
    print(json.dumps(seen, indent=2, default=str))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
