# SPDX-License-Identifier: LGPL-3.0-or-later
"""v0.14, the Claude Desktop extension (brief F): the package.

`scripts/build_desktop_extension.py` builds a `.mcpb` of the `uv` type
from a release: the app fetches uv, uv fetches Python and the
dependencies. These tests pin what the mandate asks of it:

- the manifest's version is pyproject.toml's, typed once: the template
  holds no version, and the build takes it from pyproject;
- its tool list is the server's own, asked of the server;
- the settings the app shows as a form (the tool set, the folder for
  projects, nothing secret) reach variables the server reads;
- the build is reproducible, and the package holds what uv needs;
- the manifest keeps to the MCPB manifest specification, version 0.4
  (mcpb 2.1.2's schema). CI also runs the official validator on it.
"""

import asyncio
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import build_desktop_extension as build           # noqa: E402
import qualcoder_mcp.server as server             # noqa: E402
from qualcoder_mcp import database                # noqa: E402

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10, where pytest itself needs tomli
    import tomli as tomllib

TEMPLATE = json.loads((REPO / build.TEMPLATE).read_text(encoding="utf-8"))
with open(REPO / "pyproject.toml", "rb") as _handle:
    PYPROJECT = tomllib.load(_handle)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """One build from the files on disk: (manifest, files, mcpb path)."""
    manifest, files = build.bundle(build.TreeSource(REPO))
    target = tmp_path_factory.mktemp("mcpb") / "package.mcpb"
    build.write_mcpb(files, target, build.EARLIEST_ZIP_TIME)
    return manifest, files, target


def _server_tools(mode):
    server._apply_toolset(mode)
    return [t.name for t in asyncio.run(server.mcp.list_tools())]


# ---------------------------------------------------------------------------
# Typed once
# ---------------------------------------------------------------------------

class TestTypedOnce:

    def test_the_template_holds_no_field_the_build_fills(self):
        assert set(build.GENERATED) & set(TEMPLATE) == set()
        text = (REPO / build.TEMPLATE).read_text(encoding="utf-8")
        assert PYPROJECT["project"]["version"] not in text

    def test_the_version_is_pyprojects(self, built):
        manifest, files, _ = built
        assert manifest["version"] == PYPROJECT["project"]["version"]
        assert json.loads(files["manifest.json"])["version"] == \
            PYPROJECT["project"]["version"]

    def test_the_rest_of_pyprojects_fields(self, built):
        manifest, _, _ = built
        meta = PYPROJECT["project"]
        assert manifest["name"] == meta["name"]
        assert manifest["license"] == meta["license"]
        assert manifest["keywords"] == meta["keywords"]
        assert manifest["author"]["name"] == meta["authors"][0]["name"]
        assert "email" not in manifest["author"]      # not a support channel
        assert manifest["support"] == meta["urls"]["Issues"]
        assert manifest["compatibility"]["runtimes"]["python"] == \
            meta["requires-python"]

    def test_a_typed_field_is_refused(self):
        with pytest.raises(build.BuildError, match="version"):
            build.build_manifest(dict(TEMPLATE, version="9.9.9"),
                                 PYPROJECT, [])

    def test_an_unplaced_field_is_refused(self):
        with pytest.raises(build.BuildError, match="icon"):
            build.build_manifest(dict(TEMPLATE, icon="icon.png"),
                                 PYPROJECT, [])


class TestTheToolsAreTheServers:

    def test_the_list_is_the_lifecycle_set_in_the_servers_order(self, built):
        manifest, _, _ = built
        names = [t["name"] for t in manifest["tools"]]
        assert names == _server_tools("lifecycle")
        assert "create_project" in names

    def test_every_other_set_is_inside_it(self, built):
        manifest, _, _ = built
        names = {t["name"] for t in manifest["tools"]}
        for mode in ("full", "core"):       # `full` first: `core` removes
            assert set(_server_tools(mode)) <= names, mode
        assert manifest["tools_generated"] is False

    def test_each_summary_is_one_line_from_the_description(self, built):
        manifest, _, _ = built
        descriptions = {t.name: t.description for t in
                        asyncio.run(server.mcp.list_tools())}
        for tool in manifest["tools"]:
            text = tool["description"]
            assert text and "\n" not in text
            assert len(text) <= build.SUMMARY_LIMIT
            if tool["name"] in descriptions:
                flat = " ".join(descriptions[tool["name"]].split())
                assert flat.startswith(text.rstrip(".").rstrip())

    def test_the_summary_rule(self):
        assert build.summary("One. Two.") == "One."
        assert build.summary("First line\n  goes on. Next") == \
            "First line goes on."
        assert build.summary("Para one.\n\nPara two.") == "Para one."
        assert build.summary("e.g. lower case goes on. Then") == \
            "e.g. lower case goes on."
        long = "word " * 100
        cut = build.summary(long)
        assert len(cut) <= build.SUMMARY_LIMIT and cut.endswith("...")

    def test_prompts_are_left_to_the_server(self, built):
        """The specification's prompts carry a fixed text; the server's
        four are built at call time, so none is listed and the manifest
        says the server makes them."""
        manifest, _, _ = built
        assert "prompts" not in manifest
        assert manifest["prompts_generated"] is True
        assert len(asyncio.run(server.mcp.list_prompts())) > 0


# ---------------------------------------------------------------------------
# The settings form
# ---------------------------------------------------------------------------

class TestTheSettings:

    def test_two_settings_and_nothing_secret(self):
        config = TEMPLATE["user_config"]
        assert set(config) == {"toolset", "projects_folder"}
        assert not any(option.get("sensitive") for option in config.values())
        assert not any(option.get("required") for option in config.values())

    def test_the_tool_set_defaults_to_lifecycle_and_names_the_others(self):
        option = TEMPLATE["user_config"]["toolset"]
        assert option["type"] == "string"
        assert option["default"] == "lifecycle"
        for mode in server._VALID_TOOLSET_MODES:
            assert f"{mode}:" in option["description"]

    def test_the_folder_is_a_picker_outside_documents(self):
        option = TEMPLATE["user_config"]["projects_folder"]
        assert option["type"] == "directory"
        assert "multiple" not in option
        default = option["default"]
        # `~`, which the server expands: the app does not expand a
        # ${HOME} inside a setting's default (one-pass substitution)
        assert default.startswith("~/") and "${" not in default
        assert "Documents" not in default and "Desktop" not in default

    def test_each_setting_reaches_a_variable_the_server_reads(self):
        env = TEMPLATE["server"]["mcp_config"]["env"]
        assert env == {
            "QUALCODER_MCP_TOOLSET": "${user_config.toolset}",
            database.WORKSPACE_ENV: "${user_config.projects_folder}",
        }
        source = (REPO / "src" / "qualcoder_mcp" / "server.py").read_text(
            encoding="utf-8")
        assert 'os.environ.get("QUALCODER_MCP_TOOLSET"' in source
        assert database.WORKSPACE_ENV == "QUALCODER_MCP_WORKSPACE"

    def test_the_default_folder_is_accepted_by_the_server(self, monkeypatch):
        monkeypatch.setenv(database.WORKSPACE_ENV,
                           TEMPLATE["user_config"]["projects_folder"]
                           ["default"])
        assert server._workspace_start_problem() is None
        assert database.default_workspace().parent == \
            database.standard_workspace().parent.parent      # the home

    def test_uv_starts_the_command_pyproject_installs(self):
        server_block = TEMPLATE["server"]
        assert server_block["type"] == "uv"
        config = server_block["mcp_config"]
        assert config["command"] == "uv"
        scripts = PYPROJECT["project"]["scripts"]
        assert config["args"] == ["run", "--directory", "${__dirname}",
                                  *scripts]
        assert scripts["qualcoder-mcp"] == "qualcoder_mcp.server:main"
        assert (REPO / server_block["entry_point"]).is_file()


# ---------------------------------------------------------------------------
# The package
# ---------------------------------------------------------------------------

class TestThePackage:

    def test_it_holds_what_uv_needs_and_no_more(self, built):
        _, files, target = built
        meta = PYPROJECT["project"]
        package = sorted(
            p.relative_to(REPO).as_posix()
            for p in (REPO / build.PACKAGE).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
            and p.suffix != ".pyc")
        expected = {"manifest.json", ".python-version", "pyproject.toml",
                    "uv.lock", meta["readme"], *meta["license-files"],
                    *package}
        assert set(files) == expected
        with zipfile.ZipFile(target) as z:
            assert sorted(z.namelist()) == sorted(expected)
            for name in expected - {"manifest.json", ".python-version"}:
                assert z.read(name) == (REPO / name).read_bytes(), name

    def test_it_asks_uv_for_a_python_ci_tests(self, built):
        _, files, _ = built
        assert files[".python-version"] == b"3.13\n"
        ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8")
        matrix = re.search(r"python-version: \[(.*?)\]", ci).group(1)
        assert f'"{build.PYTHON_VERSION}"' in matrix

    def test_each_entry_is_fixed(self, built):
        _, _, target = built
        with zipfile.ZipFile(target) as z:
            infos = z.infolist()
        assert [i.filename for i in infos] == sorted(i.filename
                                                     for i in infos)
        for info in infos:
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.create_system == 3
            assert info.external_attr == 0o100644 << 16
            assert info.date_time == (1980, 1, 1, 0, 0, 0)

    def test_the_same_files_give_the_same_bytes(self, built, tmp_path):
        _, files, target = built
        again = tmp_path / "again.mcpb"
        build.write_mcpb(dict(reversed(list(files.items()))), again,
                         build.EARLIEST_ZIP_TIME)
        assert again.read_bytes() == target.read_bytes()
        later = tmp_path / "later.mcpb"
        build.write_mcpb(files, later, 1790000000)
        assert later.read_bytes() != target.read_bytes()   # the date counts


class TestTheLockShippedWithIt:
    """uv.lock travels in the package, and `uv sync` installs what it
    records, so it must describe this pyproject.toml."""

    def _lock(self):
        with open(REPO / "uv.lock", "rb") as handle:
            return tomllib.load(handle)

    def _ours(self):
        return next(p for p in self._lock()["package"]
                    if p["name"] == PYPROJECT["project"]["name"])

    def test_it_records_this_version(self):
        from packaging.version import Version
        assert self._ours()["version"] == \
            str(Version(PYPROJECT["project"]["version"]))

    def test_it_records_these_requirements(self):
        lock = self._lock()
        assert lock["requires-python"] == \
            PYPROJECT["project"]["requires-python"]
        recorded = {r["name"]: r.get("specifier", "") for r in
                    self._ours()["metadata"]["requires-dist"]
                    if "marker" not in r}
        declared = {}
        for requirement in PYPROJECT["project"]["dependencies"]:
            name, spec = re.match(r"([A-Za-z0-9_.-]+)(.*)",
                                  requirement).groups()
            declared[name] = spec.replace(" ", "")
        assert recorded == declared


class TestReadingARelease:

    def test_a_commit_is_read_from_git_not_the_disk(self):
        try:
            source = build.GitSource("HEAD")
        except build.BuildError:
            pytest.skip("not a git checkout")
        assert re.fullmatch(r"[0-9a-f]{40}", source.commit)
        assert source.epoch > build.EARLIEST_ZIP_TIME
        tracked = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "HEAD", "--",
             build.PACKAGE], cwd=str(REPO), capture_output=True,
            text=True, check=True).stdout.split()
        assert source.files_under(build.PACKAGE) == sorted(tracked)
        assert source.read("pyproject.toml") == subprocess.run(
            ["git", "show", "HEAD:pyproject.toml"], cwd=str(REPO),
            capture_output=True, check=True).stdout

    def test_an_unknown_ref_is_refused_in_words(self):
        try:
            build.GitSource("HEAD")
        except build.BuildError:
            pytest.skip("not a git checkout")
        with pytest.raises(build.BuildError, match="git rev-parse"):
            build.GitSource("no-such-ref-anywhere")


# ---------------------------------------------------------------------------
# The specification: MCPB manifest 0.4, as mcpb 2.1.2's schema has it
# (src/schemas/0.4.ts, strict objects). CI runs the official validator;
# this copy of its key sets runs in every job, without Node.
# ---------------------------------------------------------------------------

SPEC_TOP = {"$schema", "dxt_version", "manifest_version", "name",
            "display_name", "version", "description", "long_description",
            "author", "repository", "homepage", "documentation", "support",
            "icon", "icons", "screenshots", "localization", "server", "tools",
            "tools_generated", "prompts", "prompts_generated", "keywords",
            "license", "privacy_policies", "compatibility", "user_config",
            "_meta"}
SPEC_OPTION = {"type", "title", "description", "required", "default",
               "multiple", "sensitive", "min", "max"}


class TestTheSpecification:

    def test_every_key_is_one_the_schema_knows(self, built):
        manifest, _, _ = built
        assert manifest["manifest_version"] == "0.4"
        assert set(manifest) <= SPEC_TOP
        assert {"name", "version", "description", "author",
                "server"} <= set(manifest)
        assert set(manifest["author"]) <= {"name", "email", "url"}
        assert set(manifest["repository"]) == {"type", "url"}
        assert set(manifest["server"]) == {"type", "entry_point",
                                           "mcp_config"}
        assert set(manifest["server"]["mcp_config"]) <= {
            "command", "args", "env", "platform_overrides"}
        assert set(manifest["compatibility"]) <= {
            "claude_desktop", "platforms", "runtimes"}
        assert set(manifest["compatibility"]["platforms"]) <= {
            "darwin", "win32", "linux"}
        assert set(manifest["compatibility"]["runtimes"]) <= {
            "python", "node"}
        for tool in manifest["tools"]:
            assert set(tool) <= {"name", "description"}
        for option in manifest["user_config"].values():
            assert set(option) <= SPEC_OPTION
            assert {"type", "title", "description"} <= set(option)
            assert option["type"] in {"string", "number", "boolean",
                                      "directory", "file"}
        for url in (manifest["homepage"], manifest["documentation"],
                    manifest["support"], manifest["repository"]["url"],
                    manifest["author"]["url"]):
            assert url.startswith("https://")

    def test_the_official_validator_is_pinned(self):
        folder = REPO / "packaging" / "desktop-extension" / "validator"
        declared = json.loads((folder / "package.json").read_text(
            encoding="utf-8"))
        assert declared["devDependencies"] == {"@anthropic-ai/mcpb": "2.1.2"}
        lock = json.loads((folder / "package-lock.json").read_text(
            encoding="utf-8"))
        entry = lock["packages"]["node_modules/@anthropic-ai/mcpb"]
        assert entry["version"] == "2.1.2"
        assert entry["integrity"].startswith("sha512-")
        assert all(p.get("integrity") for name, p in lock["packages"].items()
                   if name)


# ---------------------------------------------------------------------------
# CI builds it on every run
# ---------------------------------------------------------------------------

class TestCiBuildsIt:

    @staticmethod
    def _job(name):
        from test_v012_workflow_pins import WORKFLOWS, _jobs
        jobs = {job["name"]: "\n".join(job["lines"])
                for job in _jobs(WORKFLOWS / "ci.yml")}
        return jobs[name]

    def test_on_every_platform_from_the_commit_twice(self):
        job = self._job("desktop-extension")
        assert "os: [ubuntu-latest, windows-latest, macos-latest]" in job
        runs = re.findall(r"python scripts/build_desktop_extension\.py "
                          r"--ref HEAD --out (\S+)", job)
        assert runs == ["dist/mcpb", "dist/again"]
        assert "cmp dist/mcpb/*.mcpb dist/again/*.mcpb" in job

    def test_uploaded_before_any_third_party_tool_runs(self):
        job = self._job("desktop-extension")
        upload = job.index("actions/upload-artifact@")
        validate = job.index("npm ci --ignore-scripts")
        smoke = job.index("pip install uv==")
        assert upload < validate < smoke

    def test_the_official_validator_checks_the_manifest(self):
        job = self._job("desktop-extension")
        assert "working-directory: packaging/desktop-extension/validator" \
            in job
        assert "npx --no-install mcpb validate " \
               "../../../dist/mcpb/qualcoder-mcp-*/manifest.json" in job

    def test_it_is_installed_and_started_as_the_app_does(self):
        job = self._job("desktop-extension")
        line = next(l for l in job.splitlines()
                    if "python scripts/smoke_desktop_extension.py" in l
                    and not l.strip().startswith("#"))
        for part in ("--expect-tools manifest", "--create",
                     "--offline-restart", 'HOME="$home"',
                     'USERPROFILE="$home"'):
            assert part in line

    def test_the_platforms_give_the_same_bytes(self):
        job = self._job("desktop-extension-same")
        assert "needs: desktop-extension" in job
        assert "pattern: desktop-extension-*" in job
        assert "sort -u | wc -l)\" -eq 1" in job
