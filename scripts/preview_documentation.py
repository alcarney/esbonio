# /// script
# requires-python = ">=3.13"
# dependencies = ["textual"]
# ///
"""Simple textual app to help preview the documentation rendered by the update_<>.py scripts.

Full disclosure, Claude 4 Sonnet wrote the intiial implementation of this..
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    Markdown,
    TextArea,
)


class DirectivesBrowserApp(App):
    """A Textual app for browsing reStructuredText directives documentation."""

    CSS = """
    #list-container {
        width: 40%;
        border: solid $primary;
    }

    #markdown-container {
        overflow-y: auto;
    }

    ListView {
        height: 100%;
    }

    Markdown {
        width: 100%;
        margin: 1;
    }

    .directive-item {
        color: $text;
        padding: 0 1;
    }

    .directive-item:hover {
        background: $primary 20%;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "reload", "Reload"),
    ]

    def __init__(self, data_file: str):
        super().__init__()
        self.data = None
        self.data_file = data_file
        self.items = []
        self.load_data()

    def load_data(self):
        """Load the directives data from file."""
        try:
            with open(self.data_file, "r") as f:
                self.data = json.load(f)
            self.process_data()
        except FileNotFoundError:
            self.exit(f"Error: File '{self.data_file}' not found.")
        except json.JSONDecodeError as e:
            self.exit(f"Error: Invalid JSON in '{self.data_file}': {e}")
        except Exception as e:
            self.exit(f"Error loading '{self.data_file}': {e}")

    def process_data(self):
        """Process data into a flat list for the ListView."""
        self.items = []

        if not self.data:
            return

        # Process roles
        if self.data.get("roles"):
            for role_name, role_data in sorted(self.data["roles"].items()):
                display_name = f"[Role] {self._format_name(role_name)}"
                self.items.append(
                    {
                        "display_name": display_name,
                        "type": "role",
                        "name": role_name,
                        "data": role_data,
                    }
                )

        # Process directives - flatten the hierarchy
        directives = self.data.get("directives", {})
        for impl_name, spec in directives.items():
            self.items.append(
                {
                    "display_name": impl_name.split(".")[-1],
                    "type": "directive",
                    "name": impl_name,
                    "data": spec,
                    "group": "Directive",
                    "class_name": impl_name,
                }
            )

    def compose(self) -> ComposeResult:
        """Create child widgets for the app."""
        yield Header()
        with Horizontal():
            with Vertical(id="list-container"):
                yield ListView(id="directives-list")

            with Vertical(id="content-container"):
                with Vertical(id="markdown-container"):
                    yield Markdown(
                        "Select a directive to view its documentation.",
                        id="docs-markdown",
                    )
                yield TextArea(
                    "Raw documentation will appear here.",
                    read_only=True,
                    show_line_numbers=False,
                    id="raw-text",
                )

        yield Footer()

    async def on_mount(self) -> None:
        """Called when app starts."""
        await self.populate_list()

    async def populate_list(self) -> None:
        """Populate the ListView with directive items."""
        list_view = self.query_one("#directives-list", ListView)
        await list_view.clear()

        if not self.items:
            await list_view.append(ListItem(Label("No directives found.")))
            return

        for index, item in enumerate(self.items):
            list_item = ListItem(
                Label(item["display_name"], classes="directive-item"),
                id=f"item-{index}",
            )
            # Store the item data in the ListItem for easy access
            list_item._item_data = item
            await list_view.append(list_item)

    def _format_name(self, name: str) -> str:
        """Format a class/module name for display."""
        return name.split(".")[-1]

    async def on_list_view_highlighted(self, event: ListView.Selected) -> None:
        """Handle list item selection."""
        if not hasattr(event.item, "_item_data"):
            return

        item_data = event.item._item_data
        markdown_widget = self.query_one("#docs-markdown", Markdown)
        raw_widget = self.query_one("#raw-text", TextArea)

        # Get the documentation
        data = item_data.get("data", {})
        documentation = data.get("documentation", "No documentation available.")

        text = "\n".join(documentation)
        raw_widget.text = text
        await markdown_widget.update(text)

    async def action_reload(self) -> None:
        """Reload the data and refresh the list."""
        self.load_data()
        await self.populate_list()

        markdown_widget = self.query_one("#docs-markdown", Markdown)
        await markdown_widget.update(
            "Data reloaded. Select a directive to view its documentation."
        )


def main():
    """Main entry point."""
    import sys

    if len(sys.argv) != 2:
        print("Usage: python rst_browser.py <json_file>")
        print("Example: python rst_browser.py directives_data.json")
        sys.exit(1)

    data_file = sys.argv[1]
    app = DirectivesBrowserApp(data_file)
    app.run()


if __name__ == "__main__":
    main()
