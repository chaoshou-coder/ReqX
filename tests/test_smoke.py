import unittest

from agents.core.llm_factory import load_llm_config, redact_secrets


class TestSmoke(unittest.TestCase):
    def test_redact_secrets(self) -> None:
        raw = "authorization: Bearer sk-1234567890abcdef"
        redacted = redact_secrets(raw)
        self.assertNotIn("sk-1234567890abcdef", redacted)
        self.assertIn("<redacted>", redacted)

    def test_missing_config_non_strict(self) -> None:
        cfg = load_llm_config("this_file_should_not_exist_llm.yaml", strict=False)
        self.assertTrue(cfg.warnings)


if __name__ == "__main__":
    unittest.main()
