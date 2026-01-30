from __future__ import annotations

import importlib
import sys
import unittest
from unittest import mock


class TestOptionalDeps(unittest.TestCase):
    def test_yaml_models_imports_without_dacite(self) -> None:
        sys.modules.pop("agents.storage.yaml_models", None)
        sys.modules.pop("dacite", None)

        original_import = __import__

        def blocked_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "dacite":
                raise ModuleNotFoundError("No module named 'dacite'")
            return original_import(name, globals, locals, fromlist, level)

        with mock.patch("builtins.__import__", side_effect=blocked_import):
            m = importlib.import_module("agents.storage.yaml_models")
            self.assertTrue(hasattr(m, "parse_knowledge_payload_wire"))
            self.assertTrue(hasattr(m, "parse_transcript_payload_wire"))

