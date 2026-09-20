"""Check the packaging metadata without needing a build backend.

This validates the parts that actually break: the TOML must parse, the
declared package directory must exist, and every console-script entry point
must resolve to a real callable. It runs in milliseconds with no network.

It is a fast pre-flight, **not** a substitute for a real build. The real build
is what proves the metadata is well-formed, and it does work locally:

    cd <workspace> && python -m venv _build_venv
    cd epubkit && ../_build_venv/Scripts/python.exe -m pip install -e .
    ../_build_venv/Scripts/epubkit.exe --version
    ../_build_venv/Scripts/epubkit-mcp.exe --help

Note the venv goes *inside* the workspace. Creating one outside it gets the
process killed on this machine.

    python tests/packaging_check.py                    # structural checks
    python tests/packaging_check.py --require-install  # and demand a real install

Without `--require-install` a missing install is reported as a note, because a
fresh clone has nothing installed yet and that is not a packaging defect. With
it, absence is an error -- use that form when you mean to verify a build.
"""

import os
import re
import sys

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - dev-script convenience only
    sys.exit(
        "tests/packaging_check.py needs Python 3.11+ for tomllib.\n"
        "The library itself supports 3.10+; this dev script does not, and\n"
        "adding a tomli dependency for it would not be worth it."
    )

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

FAILURES = []

# Set by --require-install. Without it, an absent install is a note: a fresh
# clone has nothing installed and that says nothing about the packaging.
REQUIRE_INSTALL = "--require-install" in sys.argv[1:]


def check(label, condition, detail=""):
    if condition:
        print("  ok    %s" % label)
    else:
        print("  FAIL  %s %s" % (label, detail))
        FAILURES.append(label)


def _at_least(requires_python, floor):
    """Is the declared `requires-python` floor >= `floor`?

    Returns False when `requires-python` would admit versions older than the
    code needs -- e.g. `>=3.9` while the source uses a 3.10-only construct.
    Only simple `>=X.Y` forms are understood; anything else returns True so an
    unusual constraint is not failed here.
    """
    match = re.search(r">=\s*(\d+)\.(\d+)", requires_python or "")
    if not match:
        return True
    declared = (int(match.group(1)), int(match.group(2)))
    needed = tuple(int(part) for part in floor.split("."))
    return declared >= needed


def main():
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as handle:
        config = tomllib.load(handle)
    print("pyproject.toml parses")

    project = config["project"]
    build = config["build-system"]

    check("version is present", bool(project.get("version")), project.get("version"))
    check("readme file exists",
          os.path.isfile(os.path.join(ROOT, project.get("readme", ""))),
          project.get("readme"))
    check("license file exists",
          os.path.isfile(os.path.join(ROOT, "LICENSE")))
    check("zero runtime dependencies", project.get("dependencies") == [],
          repr(project.get("dependencies")))
    check("build backend declared", bool(build.get("build-backend")))

    # `requires-python` is a promise pip enforces at install time. If it is
    # lower than what the source actually needs, users on the excluded version
    # get a clean install and then a crash on import -- the worst outcome, since
    # nothing warns them. Check it against the constructs that are actually
    # version-gated in this codebase.
    requires_python = project.get("requires-python", "")
    check("requires-python is declared", bool(requires_python), requires_python)

    sources = {}
    source_root = os.path.join(ROOT, config["tool"]["setuptools"]["package-dir"][""])
    for dirpath, _dirnames, filenames in os.walk(source_root):
        for name in filenames:
            if name.endswith(".py"):
                with open(os.path.join(dirpath, name), encoding="utf-8") as handle:
                    sources[os.path.join(dirpath, name)] = handle.read()

    needs_310 = [path for path, text in sources.items() if "slots=True" in text]
    if needs_310:
        floor = "3.10"
        check("requires-python admits 3.10 constructs (%s)" % floor,
              _at_least(requires_python, floor),
              "%s uses @dataclass(slots=True), needs >=%s"
              % (os.path.basename(needs_310[0]), floor))

    needs_39 = [path for path, text in sources.items()
                if "removeprefix(" in text or "removesuffix(" in text]
    if needs_39 and not needs_310:
        check("requires-python admits 3.9 constructs",
              _at_least(requires_python, "3.9"), requires_python)

    # An `X | Y` annotation at runtime needs 3.10; `from __future__ import
    # annotations` defers evaluation and makes it safe on any version.
    annotated = [
        path for path, text in sources.items()
        if "from __future__ import annotations" not in text
        and any(token in text for token in (" | None", " -> ", ": str | "))
    ]
    if annotated:
        check("modules using `|` annotations have `from __future__`",
              not annotated, [os.path.basename(p) for p in annotated])

    # The version in pyproject must not drift from the version in the package.
    import epubkit
    check("pyproject version matches __version__",
          project["version"] == epubkit.__version__,
          "%s != %s" % (project["version"], epubkit.__version__))

    # Package discovery.
    package_dir = config["tool"]["setuptools"]["package-dir"][""]
    where = config["tool"]["setuptools"]["packages"]["find"]["where"]
    for entry in dict.fromkeys([package_dir, *where]):
        check("package dir %r exists" % entry,
              os.path.isdir(os.path.join(ROOT, entry)))

    package_root = os.path.join(ROOT, package_dir, "epubkit")
    check("src/epubkit/__init__.py exists",
          os.path.isfile(os.path.join(package_root, "__init__.py")))
    check("py.typed marker exists",
          os.path.isfile(os.path.join(package_root, "py.typed")))

    # Every module referenced by __all__ / the CLI must be importable.
    for module in ("epubkit.cli", "epubkit.mcp_server", "epubkit.api"):
        try:
            __import__(module)
            check("import %s" % module, True)
        except Exception as exc:  # noqa: BLE001 - report anything
            check("import %s" % module, False, repr(exc))

    # Console scripts must point at real callables.
    for name, target in project.get("scripts", {}).items():
        module_name, _, attribute = target.partition(":")
        try:
            module = __import__(module_name, fromlist=["_"])
            callable_ = getattr(module, attribute, None)
            check("script %s -> %s" % (name, target),
                  callable(callable_), "not callable")
        except Exception as exc:  # noqa: BLE001
            check("script %s -> %s" % (name, target), False, repr(exc))

    # Optional dependency groups must not leak into the base install.
    extras = project.get("optional-dependencies", {})
    check("extras declared", "tiktoken" in extras, list(extras))

    # The installed distribution, if this interpreter can see one. This is the
    # only part here that reflects a real build, so it is checked only when a
    # real install is actually visible.
    #
    # Careful: `importlib.metadata` finds `*.egg-info` anywhere on `sys.path`,
    # and an editable install leaves `src/epubkit.egg-info` in the tree. This
    # script puts `src` first on `sys.path`, so a naive lookup returns the
    # in-tree egg-info and *shadows* a real install -- a check that passes
    # whether or not anything was installed. So enumerate every distribution
    # named epubkit and pick out the ones that are not in-tree.
    #
    # That leftover egg-info is also why "is a distribution visible at all?" is
    # the wrong question: it answers yes on a fresh checkout of a working tree
    # that was ever installed into, and no on a clean clone, with nothing about
    # the packaging having changed. Only a non-in-tree distribution means a
    # build actually happened.
    try:
        from importlib.metadata import distributions, entry_points, requires
        from importlib.metadata import version as installed_version

        root_prefix = os.path.abspath(ROOT) + os.sep
        seen = {}
        for dist in distributions(name="epubkit"):
            origin = os.path.abspath(str(getattr(dist, "_path", "") or ""))
            seen[origin] = origin.startswith(root_prefix)

        real = sorted(origin for origin, is_tree in seen.items() if not is_tree)
        in_tree = sorted(origin for origin, is_tree in seen.items() if is_tree)

        for origin in in_tree:
            print("  note  in-tree egg-info at %s (not an install)" % origin)
        for origin in real:
            print("  note  real installed distribution at %s" % origin)

        if not real and REQUIRE_INSTALL:
            check("a distribution named epubkit is installed", False,
                  "none visible; run `pip install -e .` first")

        if real:
            check("installed version matches pyproject",
                  installed_version("epubkit") == project["version"],
                  installed_version("epubkit"))
            names = {ep.name for ep in entry_points(group="console_scripts")
                     if "epubkit" in ep.name}
            check("both console scripts are installed",
                  {"epubkit", "epubkit-mcp"} <= names, sorted(names))
            runtime = [r for r in (requires("epubkit") or []) if "extra ==" not in r]
            check("installed metadata declares no runtime dependencies",
                  runtime == [], runtime)
        else:
            print("  note  no install visible in this interpreter, so the")
            print("        installed-metadata checks were skipped. That is")
            print("        expected in a fresh clone. To verify a real build,")
            print("        run this with _build_venv/Scripts/python after")
            print("        `pip install -e .`, or pass --require-install.")
    except Exception as exc:  # noqa: BLE001 - not installed in this interpreter
        print("  note  skipped the installed-metadata checks: %r" % (exc,))

    print()
    if FAILURES:
        print("FAILED: %d check(s)" % len(FAILURES))
        return 1
    print("All packaging checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
