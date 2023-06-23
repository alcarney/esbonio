import dataclasses
import inspect
import pathlib
from typing import Any
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional
from typing import Union
from unittest import mock

from sphinx.application import Sphinx
from sphinx.cmd.build import main as sphinx_build


@dataclasses.dataclass
class SphinxConfig:
    """Configuration values to pass to the Sphinx application instance."""

    build_dir: Optional[str] = dataclasses.field(default=None)
    """The directory to write build outputs into."""

    builder_name: str = dataclasses.field(default="html")
    """The currently used builder name."""

    conf_dir: Optional[str] = dataclasses.field(default=None)
    """The directory containing the project's ``conf.py``."""

    config_overrides: Dict[str, Any] = dataclasses.field(default_factory=dict)
    """Any overrides to configuration values."""

    doctree_dir: Optional[str] = dataclasses.field(default=None)
    """The directory to write doctrees into."""

    force_full_build: bool = dataclasses.field(default=False)
    """Force a full build on startup."""

    keep_going: bool = dataclasses.field(default=False)
    """Continue building when errors (from warnings) are encountered."""

    make_mode: bool = dataclasses.field(default=True)
    """Flag indicating if the server should align to "make mode" behavior."""

    num_jobs: Union[Literal["auto"], int] = dataclasses.field(default=1)
    """The number of jobs to use for parallel builds."""

    quiet: bool = dataclasses.field(default=False)
    """Hide standard Sphinx output messages"""

    silent: bool = dataclasses.field(default=False)
    """Hide all Sphinx output."""

    src_dir: Optional[str] = dataclasses.field(default=None)
    """The directory containing the project's source."""

    tags: List[str] = dataclasses.field(default_factory=list)
    """Tags to enable during a build."""

    verbosity: int = dataclasses.field(default=0)
    """The verbosity of Sphinx's output."""

    version: Optional[str] = dataclasses.field(default=None)
    """Sphinx's version number."""

    warning_is_error: bool = dataclasses.field(default=False)
    """Treat any warning as an error"""

    @property
    def parallel(self) -> int:
        """The parsed value of the ``num_jobs`` field."""

        if self.num_jobs == "auto":
            import multiprocessing

            return multiprocessing.cpu_count()

        return self.num_jobs

    @classmethod
    def fromcli(cls, args: List[str]):
        """Return the ``SphinxConfig`` instance that's equivalent to the given arguments.

        Parameters
        ----------
        args
           The cli arguments you would normally pass to ``sphinx-build``

        Returns
        -------
        Optional[SphinxConfig]
           ``None`` if the arguments could not be parsed, otherwise the set configuration
           options derived from the sphinx build command.
        """

        if args[0] == "sphinx-build":
            args = args[1:]

        # The easiest way to handle this is to just call sphinx-build but with
        # the Sphinx app object patched out - then we just use all the args it
        # was given!
        with mock.patch("sphinx.cmd.build.Sphinx") as m_Sphinx:
            sphinx_build(args)

        if m_Sphinx.call_args is None:
            return None

        signature = inspect.signature(Sphinx)
        keys = signature.parameters.keys()

        values = m_Sphinx.call_args[0]
        sphinx_args = {k: v for k, v in zip(keys, values)}

        # `-M` has to be the first argument passed to `sphinx-build`
        # https://github.com/sphinx-doc/sphinx/blob/1222bed88eb29cde43a81dd208448dc903c53de2/sphinx/cmd/build.py#L287
        make_mode = args[0] == "-M"
        if make_mode and sphinx_args["outdir"].endswith(sphinx_args["buildername"]):
            build_dir = pathlib.Path(sphinx_args["outdir"]).parts[:-1]
            sphinx_args["outdir"] = str(pathlib.Path(*build_dir))

        if sphinx_args is None:
            return None

        return cls(
            conf_dir=sphinx_args.get("confdir", None),
            config_overrides=sphinx_args.get("confoverrides", {}),
            build_dir=sphinx_args.get("outdir", None),
            builder_name=sphinx_args.get("buildername", "html"),
            doctree_dir=sphinx_args.get("doctreedir", None),
            force_full_build=sphinx_args.get("freshenv", False),
            keep_going=sphinx_args.get("keep_going", False),
            make_mode=make_mode,
            num_jobs=sphinx_args.get("parallel", 1),
            quiet=sphinx_args.get("status", 1) is None,
            silent=sphinx_args.get("warning", 1) is None,
            src_dir=sphinx_args.get("srcdir", None),
            tags=sphinx_args.get("tags", []),
            verbosity=sphinx_args.get("verbosity", 0),
            warning_is_error=sphinx_args.get("warningiserror", False),
        )

    def to_application_args(self) -> Dict[str, Any]:
        """Convert this into the equivalent Sphinx application arguments."""

        if self.make_mode:
            outdir = str(pathlib.Path(self.build_dir, self.builder_name))
            doctree_dir = str(pathlib.Path(self.build_dir, "doctrees"))
        else:
            outdir = self.build_dir
            doctree_dir = self.doctree_dir

        return {
            "buildername": self.builder_name,
            "confdir": self.conf_dir,
            "confoverrides": self.config_overrides,
            "doctreedir": doctree_dir,
            "freshenv": self.force_full_build,
            "keep_going": self.keep_going,
            "outdir": outdir,
            "parallel": self.parallel,
            "srcdir": self.src_dir,
            "status": None,
            "tags": self.tags,
            "verbosity": self.verbosity,
            "warning": None,
            "warningiserror": self.warning_is_error,
        }