# /// script
# requires-python = ">=3.13"
# dependencies = ["docutils", "httpx", "platformdirs", "sphinx"]
# ///
"""Script to produce an index of known role and directive implementations from
sphinx.

This index contains information such as
- documentation
- known arguments/options

The output of this is NOT meant to be comprehensive, but to automate the parts that can
be automated, allowing people to focus on the last 20%.

"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import tempfile
import typing
from urllib.parse import urlparse

import httpx
import platformdirs
from docutils import nodes
from docutils.core import publish_doctree, publish_from_doctree
from docutils.parsers import rst
from docutils.parsers.rst import directives, roles
from docutils.utils import new_document
from docutils.writers import Writer
from sphinx.application import Sphinx
from sphinx.util.docutils import CustomReSTDispatcher, SphinxDirective
from sphinx.util.inventory import InventoryFile

if typing.TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

    from sphinx.ext.intersphinx import Inventory


DIRECTIVES_DOC_URL = "https://docutils.sourceforge.io/docs/ref/rst/directives.rst"
ROLES_DOC_URL = "https://docutils.sourceforge.io/docs/ref/rst/roles.rst"
LOG_LEVELS = [logging.WARNING, logging.INFO, logging.DEBUG]

SECTION_ID_MAP = {
    "class": "class-20",
    "contents": "table-of-contents",
    "compound": "compound-paragraph",
}


cli = argparse.ArgumentParser(
    description="update esbonio's index of sphinx roles and directives"
)
cli.add_argument(
    "--skip-cache", action="store_true", help="bypass the cache and fetch from source"
)
cli.add_argument("--debug", action="store_true", help="run in debug mode")
cli.add_argument(
    "-o", "--output", default=None, type=str, help="the output file to write to"
)
cli.add_argument(
    "-v", "--verbose", action="count", default=0, help="enable verbose output."
)


def main(argv: Sequence[str] | None = None) -> int | None:
    args = cli.parse_args(argv)

    if not args.output:
        cli.print_help()
        sys.exit(1)

    logging.basicConfig(
        format="[%(levelname)s]: %(message)s",
        level=LOG_LEVELS[min(args.verbose, len(LOG_LEVELS) - 1)],
    )

    if not (output := pathlib.Path(args.output)).exists():
        existing = {"roles": {}, "directives": {}}
    else:
        existing = json.loads(output.read_text())

    with tempfile.TemporaryDirectory() as tmpdir:
        srcdir = pathlib.Path(tmpdir)
        outdir = srcdir / "_build"

        # Create an index.rst for sphinx to process
        (srcdir / "index.rst").write_text("Hello World\n===========\n")

        app = Sphinx(
            srcdir=srcdir,
            confdir=None,
            outdir=outdir,
            doctreedir=outdir / ".doctrees",
            buildername="html",
            confoverrides={
                "extensions": [
                    "sphinx.ext.intersphinx",
                ],
            },
        )
        app.build()

    # objects.inv is amazing, it's useful in so many ways! With it we can
    # - automatically discover all the (documented!) roles and directives that Sphinx provides.
    # - ensure all cross-referencing roles resolve correctly, by providing implementations that
    #   use it look up the correct url.
    # - ensure all descriptive directives produce sections with sensible ids we can use when
    #   selecting subsections of doctrees to render out.
    base_url = "https://www.sphinx-doc.org/en/master"
    inv_url = f"{base_url}/objects.inv"
    if (objects_inv := get_remote_file(inv_url)) is None:
        logging.error("Unable to fetch intersphinx inventory")
        return 1

    inv = InventoryFile.loads(objects_inv.read_bytes(), uri=base_url)
    roles = existing["roles"]
    directives = update_directives(app, inv, existing["directives"])

    index = {"roles": roles, "directives": directives}
    output.write_text(json.dumps(index, indent=2))


def update_directives(
    app: Sphinx, inv: Inventory, directive_index: dict[str, dict[str, Any]]
):
    """Updates the existing ``directive_index`` with any new implementations/options.

    It also attempts to grab the latest documentation from the docutils documentation.
    Most importantly however, this does NOT overwrite any existing data allowing for
    additional details to be provided by hand.
    """
    impls = {
        **directives._directive_registry,
        **directives._directives,
    }

    for name, domain in app.env.domains.items():
        for directive, impl in domain.directives.items():
            impls[f"{domain.name}:{directive}"] = impl

    for name, loc in inv.data["rst:directive"].items():
        if name not in impls:
            logging.warning("No implementation found for: %r", name)
            continue

        uri = urlparse(loc.uri)
        path = uri.path.replace("/en/master/", "").replace(".html", ".rst")
        src_url = f"https://github.com/sphinx-doc/sphinx/raw/master/doc/{path}"
        logging.debug("%r -> %r", loc.uri, src_url)

        if (docsource := get_remote_file(src_url)) is None:
            logging.warning("Unable to fetch documentation source: %r", loc.uri)
            continue

        # Parse the file
        with patch_impls():
            doctree: nodes.document = publish_doctree(
                docsource.read_text(), source_path=src_url
            )

        # Despite setting source_path above, the doucment source is not set...
        doctree.source = loc.uri
        section = doctree.next_node(find_section(uri.fragment))
        breakpoint()

    # for k, spec in impls.items():
    #     impl, dotted_name = resolve_directive(spec)

    #     # Make sure we don't stomp on any existing records
    #     record = directive_index.setdefault(dotted_name, new_directive())
    #     update_documentation(record, doctree, k)

    #     # Ensure options are up to date
    #     for opt in (impl.option_spec or {}).keys():
    #         record["option_providers"].setdefault(opt, None)

    return directive_index


def update_roles(role_index: dict[str, dict[str, Any]]):
    """Updates the existing ``role_index`` with any new implementations/options.

    It also attempts to grab the latest documentation from the docutils documentation.
    Most importantly however, this does NOT overwrite any existing data allowing for
    additional details to be provided by hand.
    """
    items = {
        **roles._roles,
        **roles._role_registry,
    }

    if (text := get_remote_file(ROLES_DOC_URL)) is not None:
        # Hack for excluding the .. include:: ../../header2.rst directive at the top of the
        # file.
        text = text[text.find("==") :]

        # Parse the file
        doctree: nodes.document = publish_doctree(text, source_path=ROLES_DOC_URL)

        # Despite setting source_path above, the doucment source is not set...
        doctree.source = ROLES_DOC_URL.replace(".rst", ".html")

    for k, impl in items.items():
        try:
            dotted_name = f"{impl.__module__}.{impl.__name__}"
        except AttributeError:
            dotted_name = f"{impl.__module__}.{impl.__class__.__name__}"

        # Make sure we don't stomp on any existing records
        record = role_index.setdefault(dotted_name, new_role())
        update_documentation(record, doctree, k)

    return role_index


def get_remote_file(url: str, skip_cache: bool = False) -> pathlib.Path | None:
    """Fetch a remote file, from the given url.

    Cache the file locally so that we don't have to repeatedly hit the network.
    """

    uri = urlparse(url)
    logging.debug("URL: %r", uri)

    cache_dir = platformdirs.user_cache_path(appname="esbonio-dev", appauthor="swyddfa")
    cache_name = cache_dir / uri.netloc / uri.path[1:]
    logging.debug("Cache filepath: %s", cache_name)

    if cache_name.exists() and not skip_cache:
        logging.info("Reading %r from cache", url)
        return cache_name

    try:
        logging.info("Reading %s from network", url)

        if not cache_name.parent.exists():
            cache_name.parent.mkdir(parents=True)

        response = httpx.get(url, follow_redirects=True).raise_for_status()
        cache_name.write_bytes(response.content)
        return cache_name

    except Exception:
        logging.exception(
            "Unable to fetch documentation, no documentation updates will be made"
        )
        return None


def update_documentation(
    record: dict[str, Any], doctree: nodes.document | None, item: str
):
    """Update the documentation for the given item."""
    logging.debug("Documenting item: %r", item)

    if doctree is None:
        return

    section_id = SECTION_ID_MAP.get(item, item)

    if (node := doctree.next_node(condition=find_section(section_id))) is None:
        logging.warning("Unable to find section node for %r", item)
        return

    source = doctree.source or "<document>"

    document = new_document(source)
    document += node

    record["source"] = f"{source}#{section_id}"
    record["license"] = "https://docutils.sourceforge.io/COPYING.html"
    record["documentation"] = publish_from_doctree(
        document, writer=MarkdownWriter(source)
    )


def new_directive():
    """Return a new directive record."""
    return {
        "documentation": "",
        "option_providers": {},
        "argument_providers": [],
        "source": "",
        "license": "",
    }


def new_role():
    """Return a new role record."""
    return {
        "documentation": "",
        "argument_providers": [],
        "source": "",
        "license": "",
    }


def find_section(id: str):
    """Return a function that selects a section in a doctree based on the given od."""

    def match_node(node: nodes.Node):
        if isinstance(node, nodes.section):
            return id in node["ids"]

        if isinstance(node, nodes.target):
            return id == node.attributes.get("refid")

        return False

    return match_node


def run_noop(self):
    return []


def run_parse(self):
    result = []

    if len(self.arguments) > 0:
        src = " ".join(self.arguments)
        if ".." in src and "::" in src:
            result.append(nodes.target(src, refid=self.arguments[0]))

    result.extend(self.parse_content_to_nodes())
    return result


def role_noop(*args, **kwargs):
    return [], []


class patch_impls(CustomReSTDispatcher):
    """This patches the implementation of many roles and directives so that they work
    outside the context of a Sphinx build.

    """

    def directive(self, directive_name, language_module, document):
        impl, _ = self.directive_func(directive_name, language_module, document)
        if impl is None:
            # Fallback to some sensible defaults.
            has_content = True
            option_spec = None
            required_arguments = 0
            optional_arguments = 1
            final_argument_whitespace = True
        else:
            # Mimic the "shape" of the real directive
            if impl.option_spec is None:
                option_spec = None
            else:
                option_spec = {o: directives.unchanged for o in impl.option_spec}

            # It probably doesn't make sense to copy these values, as often the user's
            # usage of a directive will be incorrect.
            required_arguments = 0  # impl.required_arguments
            has_content = True  # impl.has_content

            optional_arguments = 1
            final_argument_whitespace = True

        attrs = {
            "has_content": has_content,
            "option_spec": option_spec,
            "required_arguments": required_arguments,
            "optional_arguments": optional_arguments,
            "final_argument_whitespace": final_argument_whitespace,
            "run": run_parse,
        }
        return type("DummyDirective", (SphinxDirective,), attrs), []

    def role(self, role_name, language_module, lineno, reporter):
        impl, _ = self.role_func(role_name, language_module, lineno, reporter)
        if impl is None:
            impl = role_noop
        return impl, []


class MarkdownWriter(Writer):
    supported = ("markdown",)

    def __init__(self, url: str) -> None:
        super().__init__()
        self.translator_class = MarkdownTranslator
        self.url = url

    def translate(self):
        visitor = self.translator_class(self.document, self.url)
        self.document.walkabout(visitor)
        self.output = visitor.lines


@typing.final
class MarkdownTranslator(nodes.NodeVisitor):
    """Walk a doctree converting it to markdown."""

    def __init__(self, document: nodes.document, url: str) -> None:
        super().__init__(document)
        self.level = 0
        self.current_text = ""
        self.lines: list[str] = []
        self.url = url

    def commit_text(self):
        self.lines.extend(self.current_text.split("\n"))
        self.current_text = ""

    def astext(self):
        return "\n".join(self.lines)

    @typing.override
    def visit_document(self, node: nodes.document):
        pass

    @typing.override
    def depart_document(self, node: nodes.document):
        pass

    @typing.override
    def visit_section(self, node: nodes.section):
        self.level += 1

    @typing.override
    def depart_section(self, node: nodes.section):
        self.level -= 1

    @typing.override
    def visit_title(self, node: nodes.title):
        if isinstance(node.parent, nodes.section):
            self.current_text += f"{'#' * self.level} "

        elif isinstance(node.parent, (nodes.admonition, nodes.topic)):
            pass

        else:
            logging.warning("title node in unknown context: %r", node.parent)

    @typing.override
    def visit_paragraph(self, node: nodes.paragraph):
        ignored_nodes = (nodes.list_item, nodes.field_body)
        if not isinstance(node.parent, ignored_nodes):
            self.commit_text()

    @typing.override
    def depart_paragraph(self, node: nodes.paragraph):
        if not isinstance(node.parent, nodes.field_body):
            self.commit_text()

    # -------------------------------- Admonitions-------------------------------------

    @typing.override
    def visit_admonition(self, node: nodes.admonition):
        title = node.next_node(nodes.title)
        self._admonition_visit(title.astext())

    @typing.override
    def depart_admonition(self, node: nodes.admonition):
        self._admonition_depart()

    @typing.override
    def visit_block_quote(self, node: nodes.block_quote):
        self._admonition_visit("")

    @typing.override
    def depart_block_quote(self, node: nodes.block_quote):
        self._admonition_depart()

    @typing.override
    def visit_caution(self, node: nodes.caution):
        self._admonition_visit("caution")

    @typing.override
    def depart_caution(self, node: nodes.caution):
        self._admonition_depart()

    @typing.override
    def visit_note(self, node: nodes.note):
        self._admonition_visit("note")

    @typing.override
    def depart_note(self, node: nodes.note):
        self._admonition_depart()

    @typing.override
    def visit_tip(self, node: nodes.tip):
        self._admonition_visit("tip")

    @typing.override
    def depart_tip(self, node: nodes.tip):
        self._admonition_depart()

    @typing.override
    def visit_topic(self, node: nodes.topic):
        title = node.next_node(nodes.title)
        node.children.remove(title)
        self._admonition_visit(title.astext())

    @typing.override
    def depart_topic(self, node: nodes.topic):
        self._admonition_depart()

    @typing.override
    def visit_warning(self, node: nodes.warning):
        self._admonition_visit("warning")

    @typing.override
    def depart_warning(self, node: nodes.warning):
        self._admonition_depart()

    def _admonition_visit(self, name: str):
        self.commit_text()
        title = f"**{name.upper()}**" if len(name) > 0 else ""
        self.current_text = f"----\n{title}\n"
        self.commit_text()

    def _admonition_depart(self):
        self.commit_text()
        self.current_text = "----"
        self.commit_text()

    # -------------------------------- References -------------------------------------

    @typing.override
    def visit_reference(self, node: nodes.reference):
        self.current_text += "["

    @typing.override
    def depart_reference(self, node: nodes.reference):
        uri = node.get("refuri", None)

        if not uri:
            anchor = node.get("refid", None)
            url = f"{self.url}#{anchor}"
        elif not uri.startswith("http"):
            base = pathlib.Path(self.url).parent
            url = f"{base}/{uri}"
        else:
            url = uri

        self.current_text += f"]({url})"

    @typing.override
    def visit_target(self, node: nodes.target):
        pass

    @typing.override
    def visit_substitution_definition(self, node: nodes.substitution_definition):
        pass

    # -------------------------------- Bullet Lists -----------------------------------

    @typing.override
    def visit_bullet_list(self, node: nodes.bullet_list):
        pass

    @typing.override
    def visit_list_item(self, node: nodes.list_item):
        self.current_text += "- "

    # -------------------------------- Definition Lists -------------------------------

    @typing.override
    def visit_definition_list(self, node: nodes.definition_list):
        self.commit_text()

    @typing.override
    def depart_definition_list(self, node: nodes.definition_list):
        pass

    @typing.override
    def visit_definition_list_item(self, node: nodes.definition_list_item):
        # Get the name of the thing we're defining
        first = node.children.pop(0)

        if not isinstance(first, nodes.term):
            raise RuntimeError(f"Expected node 'term', got '{type(node)}'")

        self.key = first.astext()
        self.current_text += f"`{self.key}`: "

    @typing.override
    def depart_definition_list_item(self, node: nodes.definition_list_item):
        self.commit_text()

    @typing.override
    def visit_definition(self, node: nodes.definition):
        pass

    @typing.override
    def visit_classifier(self, node: nodes.classifier):
        pass

    @typing.override
    def depart_classifier(self, node: nodes.classifier):
        self.commit_text()

    # -------------------------------- Field Lists ------------------------------------

    @typing.override
    def visit_field_list(self, node: nodes.field_list):
        self.current_text += "\n| | |\n|-|-|"
        self.commit_text()

    @typing.override
    def visit_field_name(self, node: nodes.field_name):
        self.current_text += "| "

    @typing.override
    def depart_field_name(self, node: nodes.field_name):
        self.current_text += " | "

    @typing.override
    def visit_field(self, node: nodes.field):
        pass

    @typing.override
    def depart_field(self, node: nodes.field):
        self.current_text += " |"
        self.commit_text()

    @typing.override
    def visit_field_body(self, node: nodes.field_body):
        pass

    @typing.override
    def depart_field_body(self, node: nodes.field_body):
        self.current_text = self.current_text.replace("\n", "")
        pass

    # --------------------------------- Footnotes -------------------------------------

    @typing.override
    def visit_footnote_reference(self, node: nodes.footnote_reference):
        self.current_text += "[^"

    @typing.override
    def depart_footnote_reference(self, node: nodes.footnote_reference):
        self.current_text += "]"

    @typing.override
    def visit_footnote(self, node: nodes.footnote):
        pass

    @typing.override
    def depart_footnote(self, node: nodes.footnote):
        self.commit_text()

    @typing.override
    def visit_label(self, node: nodes.label):
        self.current_text += "[^"

    @typing.override
    def depart_label(self, node: nodes.label):
        self.current_text += "]: "

    # --------------------------------- Misc ------------------------------------------

    @typing.override
    def visit_attribution(self, node: nodes.attribution):
        self.commit_text()
        self.current_text = "-- "

    @typing.override
    def depart_attribution(self, node: nodes.attribution):
        self.commit_text()

    @typing.override
    def visit_comment(self, node: nodes.comment):
        node.children = []

    @typing.override
    def visit_literal(self, node: nodes.literal):
        self.current_text += "`"

    @typing.override
    def depart_literal(self, node: nodes.literal):
        self.current_text += "`"

    @typing.override
    def visit_literal_block(self, node: nodes.literal_block):
        self.lines.append("```")

    @typing.override
    def depart_literal_block(self, node: nodes.literal_block):
        self.commit_text()
        self.lines.append("```")

    @typing.override
    def visit_strong(self, node: nodes.strong):
        self.current_text += "**"

    @typing.override
    def depart_strong(self, node: nodes.strong):
        self.current_text += "**"

    @typing.override
    def visit_emphasis(self, node: nodes.emphasis):
        self.current_text += "*"

    @typing.override
    def depart_emphasis(self, node: nodes.emphasis):
        self.current_text += "*"

    @typing.override
    def visit_Text(self, node: nodes.Text):
        if isinstance(node.parent, nodes.substitution_definition):
            return

        self.current_text += node.astext()

    @typing.override
    def unknown_visit(self, node):
        logging.warning("skipping unknown node: '%s'", node.__class__.__name__)

    @typing.override
    def unknown_departure(self, node):
        pass


if __name__ == "__main__":
    sys.exit(main())
