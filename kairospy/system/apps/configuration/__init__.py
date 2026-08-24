"""Workspace configuration application package.

Import concrete APIs from ``kairospy.system.apps.configuration.application``.
Keeping this package initializer lazy avoids a cycle when credential storage
uses the configuration transaction service.
"""
