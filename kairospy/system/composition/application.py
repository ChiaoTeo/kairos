"""Construct the aggregate System application for one workspace."""

from kairospy.system.application import SystemApplication
from kairospy.system.apps.components.application import ComponentProcessApplication
from kairospy.system.apps.configuration.application import ConfigApplication
from kairospy.system.apps.credentials.application import CredentialConfigurationApplication
from kairospy.system.apps.integration.application import IntegrationCliApplication
from kairospy.system.apps.launch.application import LaunchRuntimeApplication
from kairospy.system.apps.launch.composition import compose_strategy_process
from kairospy.system.apps.operations.application import OperationJournal
from kairospy.system.apps.workspace.application import Workspace, WorkspaceApplication


def compose_system_application(workspace: Workspace) -> SystemApplication:
    return SystemApplication(
        workspace=WorkspaceApplication(),
        credentials=CredentialConfigurationApplication(workspace),
        configuration=ConfigApplication(workspace),
        operations=OperationJournal(workspace),
        launch=LaunchRuntimeApplication(workspace),
        components=ComponentProcessApplication(workspace),
        integration=IntegrationCliApplication(),
        strategy_process_factory=lambda **request: compose_strategy_process(
            workspace, **request
        ),
    )
