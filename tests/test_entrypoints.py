from __future__ import annotations

import unittest
from unittest import mock


class TestEntrypoints(unittest.TestCase):
    def test_agents_cli_main_is_callable(self) -> None:
        from agents.cli import main

        self.assertTrue(callable(main))

    def test_install_main_spawns_on_windows_when_called_from_reqx_exe(self) -> None:
        from agents.cli.admin import install_main

        with (
            mock.patch("agents.cli.admin.os.name", "nt"),
            mock.patch("agents.cli.admin.sys.argv", ["C:\\Python\\Scripts\\reqx.exe"]),
            mock.patch("agents.cli.admin.subprocess.Popen") as popen,
            mock.patch("agents.cli.admin.subprocess.check_call") as check_call,
        ):
            code = install_main(no_deps=True)
            self.assertEqual(code, 0)
            popen.assert_called_once()
            check_call.assert_not_called()
