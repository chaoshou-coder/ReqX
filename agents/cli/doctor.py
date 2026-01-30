from __future__ import annotations

import os
from pathlib import Path
import sys

import yaml

from ..core.llm_factory import load_llm_config


def doctor_main(*, config_path: str | None) -> int:
    cfg = load_llm_config(config_path, strict=True)
    provider = cfg.provider.lower().strip()
    resolved_config_path = Path(config_path) if config_path is not None else Path(os.environ["LLM_CONFIG_PATH"])
    config_dir = resolved_config_path.parent if resolved_config_path.parent else Path.cwd()
    forced_env = os.getenv("LLM_ENV_PATH")
    env_path: Path | None = None
    if forced_env:
        env_path = Path(forced_env)
    elif cfg.env_file:
        env_file_path = Path(cfg.env_file)
        env_path = env_file_path if env_file_path.is_absolute() else (config_dir / cfg.env_file)

    if provider in {"azure", "azure_openai"}:
        key_env = cfg.azure_api_key_env or ""
    else:
        key_env = cfg.api_key_env

    payload = {
        "schema_version": 1,
        "config_path": str(resolved_config_path),
        "provider": cfg.provider,
        "model": cfg.model,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "input_char_limit": cfg.input_char_limit,
        "output_char_limit": cfg.output_char_limit,
        "env_file": cfg.env_file,
        "env_path_effective": str(env_path) if env_path else None,
        "api_key_env": key_env,
        "api_key_present_in_process_env": bool(os.getenv(key_env)) if key_env else False,
        "base_url": cfg.base_url,
        "azure_endpoint": cfg.azure_endpoint,
        "azure_deployment": cfg.azure_deployment,
        "azure_api_version": cfg.azure_api_version,
        "warnings": list(cfg.warnings),
    }

    sys.stdout.write(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
    return 0

