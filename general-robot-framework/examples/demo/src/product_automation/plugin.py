import argparse
import json

from grf.pipeline.ingest import build_catalog, load_scope, write_catalog
from grf.plugin import Command, ProductPlugin


class DemoPlugin(ProductPlugin):
    """Adds only the synthetic demo ingestion command; normal ingest still uses RV&S."""

    def add_commands(self, sub: argparse._SubParsersAction) -> dict[str, Command]:
        sub.add_parser("demo-ingest", help="build the demo catalog from the local fake RV&S fixture")
        return {"demo-ingest": self.demo_ingest}

    def demo_ingest(self, args: argparse.Namespace) -> int:
        fixture = json.loads((self.project.root / "fixtures/rvs.json").read_text(encoding="utf-8"))
        scope = load_scope(self.project.path("scope"))
        catalog = build_catalog(scope, fixture["specifications"], fixture["requirements"],
                                fixture["userStories"], fixture["explorativeTests"])
        catalog["source"] = "Synthetic RV&S fixture (demo only)"
        output = self.project.path("catalog_dir") / f"{scope['name']}.json"
        write_catalog(output, catalog)
        print(f"Demo catalog written to {output}")
        return 0
