"""Deployment of a product build from Jenkins (or a local tarball) to a board as a Docker container.

GRF holds the generic part: the model (``model``), the site configuration outside the repository (``config``), the
Jenkins client with checksum and provenance (``jenkins``), the ssh transport (``transport``), the Docker recipe
(``docker``), the step engine with lock, health check and automatic rollback (``engine``) and the deployment record
(``record``). A product lists what it can deploy in ``ProductPlugin.deploy_components()``. See docs\\DEPLOYMENT.md.
"""

from grf.deploy.model import EXIT_CODES, Component, ContainerRecipe, DeployError, Mount, Outcome

__all__ = ["EXIT_CODES", "Component", "ContainerRecipe", "DeployError", "Mount", "Outcome"]
