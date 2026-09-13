"""Create the Roles this app's DocPerms name, on sites that already have it installed.

after_install covers a fresh `bench install-app`; this covers `bench migrate` on
a site where the app is already installed. Both call the same idempotent
function — see upande_irrigation/install.py for why roles are created rather
than the DocPerms being widened to System Manager.
"""

from upande_irrigation.install import ensure_roles


def execute():
	ensure_roles()
