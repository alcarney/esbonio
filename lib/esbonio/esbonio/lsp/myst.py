"""MyST Support."""
import re
from typing import List
from typing import Optional

from pygls.lsp.types import CompletionItem
from pygls.lsp.types import Location

from esbonio.lsp.directives import Directives
from esbonio.lsp.roles import Roles
from esbonio.lsp.rst import CompletionContext
from esbonio.lsp.rst import DefinitionContext
from esbonio.lsp.rst import LanguageFeature
from esbonio.lsp.rst import RstLanguageServer
from esbonio.lsp.sphinx import SphinxLanguageServer


ROLE = re.compile(
    r"""
    ([^\w:]|^\s*)                     # roles cannot be preceeded by letter chars
    (?P<role>
      {                               # roles begin with a '{' character
      ((?P<domain>[\w]+):)?           # roles may include a domain
      ((?P<name>[\w-]+)}?)?           # roles have a name
      )
    (?P<target>
      `                               # targets begin with a '`' character
      ((?P<alias>[^<`>]*?)<)?         # targets may specify an alias
      (?P<modifier>[!~])?             # targets may have a modifier
      (?P<label>[^<`>]*)?             # targets contain a label
      >?                              # labels end with a '>' when there's an alias
      `?                              # targets end with a '`' character
    )?
    """,
    re.VERBOSE,
)

DIRECTIVE = re.compile(r"", re.VERBOSE)


class MyST(LanguageFeature):

    definition_triggers = [ROLE, DIRECTIVE]
    completion_triggers = [ROLE, DIRECTIVE]

    def __init__(
        self,
        rst: RstLanguageServer,
        roles: Optional[Roles],
        directives: Optional[Directives],
        *args,
        **kwargs
    ):
        super().__init__(rst, *args, **kwargs)

        self.directives = directives
        self.roles = roles

    def definition(self, context: DefinitionContext) -> List[Location]:
        return super().definition(context)

    def complete(self, context: CompletionContext) -> List[CompletionItem]:
        """Generate completion suggestions relevant to the current context.

        This function is a little intense, but its sole purpose is to determine the
        context in which the completion request is being made and either return
        nothing, or the results of :meth:`~esbonio.lsp.roles.Roles.complete_roles` or
        :meth:`esbonio.lsp.roles.Roles.complete_targets` whichever is appropriate.

        Parameters
        ----------
        context:
           The context of the completion request.
        """
        if context.location not in {"markdown", "docstring"}:
            return []

        groups = context.match.groupdict()

        if "role" in groups:
            return self.complete_roles(context)

        return []

    def complete_roles(self, context: CompletionContext) -> List[CompletionItem]:

        if not self.roles:
            return []

        # All text matched by the regex
        text = context.match.group(0)
        start, end = context.match.span()
        target = context.match.group("target")

        if target:
            target_index = start + text.find(target)

            # Only trigger target completions if the request was made from within
            # the target part of the role.
            if target_index <= context.position.character <= end:
                return self.roles.complete_targets(context)

        items = self.roles.complete_roles(context)

        return items

    def completion_resolve(self, item: CompletionItem) -> CompletionItem:
        return super().completion_resolve(item)


def esbonio_setup(rst: RstLanguageServer):

    if not isinstance(rst, SphinxLanguageServer):
        return

    roles = rst.get_feature("esbonio.lsp.roles.Roles")
    directives = rst.get_feature("esboino.lsp.directives.Directives")

    rst.add_feature(MyST(rst=rst, roles=roles, directives=directives))
