import argparse
import os
from pathlib import Path
import sys
import threading
import time
from contextlib import contextmanager
import shutil

from crewai import Agent, Crew, LLM, Task
import httpx

from agents import RequirementExcavationSkill, get_llm
from agents.llm_factory import load_llm_config, redact_secrets


def _timeout_seconds() -> float:
    raw = (os.getenv("LLM_HTTP_TIMEOUT_S") or "").strip()
    if not raw:
        return 180.0
    try:
        v = float(raw)
        return v if v > 0 else 180.0
    except Exception:
        return 180.0


def _resolve_config_path(config_path: str | None) -> Path:
    if config_path:
        return Path(config_path)
    forced = os.getenv("LLM_CONFIG_PATH")
    if forced:
        return Path(forced)
    raise RuntimeError("缺少配置文件路径：请使用 --config 或设置 LLM_CONFIG_PATH")


def _resolve_env_path(*, env_file: str, config_dir: Path) -> Path:
    forced = os.getenv("LLM_ENV_PATH")
    if forced:
        return Path(forced)
    p = Path(env_file)
    if p.is_absolute():
        return p
    return config_dir / env_file


def _heartbeat(label: str, *, interval_s: float = 5.0) -> threading.Event:
    stop = threading.Event()
    started = time.monotonic()

    def _run() -> None:
        is_tty = bool(getattr(sys.stderr, "isatty", lambda: False)())
        last_len = 0
        while not stop.wait(interval_s):
            elapsed = int(time.monotonic() - started)
            msg = f"[{elapsed:>4}s] {label}"
            if is_tty:
                padding = max(0, last_len - len(msg))
                sys.stderr.write("\r" + msg + (" " * padding))
                sys.stderr.flush()
                last_len = len(msg)
            else:
                sys.stderr.write(msg + "\n")
                sys.stderr.flush()

    t = threading.Thread(target=_run, name="run_agent_heartbeat", daemon=True)
    t.start()
    return stop


@contextmanager
def _step(title: str, *, heartbeat: str | None = None):
    print(title, flush=True)
    stop: threading.Event | None = None
    heartbeat_active = False
    if heartbeat:
        stop = _heartbeat(heartbeat)
        heartbeat_active = True
    try:
        yield
        print(f"{title} 完成。", flush=True)
    finally:
        if stop:
            stop.set()
            if heartbeat_active and bool(getattr(sys.stderr, "isatty", lambda: False)()):
                width = int(getattr(shutil.get_terminal_size(fallback=(120, 24)), "columns", 120))
                sys.stderr.write("\r" + (" " * max(20, width)) + "\r")
                sys.stderr.flush()


def _resolve_max_tokens(cfg_max_tokens: int | None, arg_max_tokens: int | None) -> int:
    max_tokens = cfg_max_tokens
    if max_tokens is None or max_tokens > 1024:
        max_tokens = 1024
    if isinstance(arg_max_tokens, int) and arg_max_tokens > 0:
        max_tokens = min(1024, int(arg_max_tokens))
    return int(max_tokens)


def _tool_run(tool: object, surface: str) -> str:
    runner = getattr(tool, "run", None)
    if callable(runner):
        return str(runner(surface))
    return str(getattr(tool, "_run")(surface))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run requirement agent demo")
    p.add_argument("--config", default=None, help="配置文件路径（不提供则使用环境变量 LLM_CONFIG_PATH）")
    p.add_argument(
        "--mode",
        default=None,
        choices=["direct", "crew"],
        help="运行模式：direct=直接调用 Tool；crew=CrewAI 编排（默认读取 RUN_AGENT_MODE，否则 direct）",
    )
    p.add_argument("--surface", default=None, help="表层问题描述（默认使用内置示例）")
    p.add_argument("--max-tokens", default=None, type=int, help="覆盖生成最大 tokens（默认上限 1024）")
    p.add_argument("--skip-connectivity-test", action="store_true", help="跳过 GET /models 连通性测试")
    p.add_argument("--connectivity-timeout-s", default=20.0, type=float, help="连通性测试超时秒数（默认 20）")
    p.add_argument("--no-openai-key-map", action="store_true", help="不将 cfg.api_key_env 映射到 OPENAI_API_KEY")
    return p


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    with _step("加载 LLM 配置"):
        cfg = load_llm_config(args.config, strict=True)

    resolved_config_path = _resolve_config_path(args.config)
    config_dir = resolved_config_path.parent if resolved_config_path.parent else Path.cwd()
    resolved_env_path = _resolve_env_path(env_file=cfg.env_file or "", config_dir=config_dir) if cfg.env_file else None
    base_url_display = (cfg.base_url or "").strip().strip("`").strip("'").strip('"').strip()
    print(
        "有效配置：\n"
        f"- config_path: {resolved_config_path}\n"
        f"- env_path: {resolved_env_path}\n"
        f"- provider: {cfg.provider}\n"
        f"- model: {cfg.model}\n"
        f"- base_url: {base_url_display or cfg.base_url}\n",
        flush=True,
    )

    with _step("初始化 LLM 客户端"):
        max_tokens = _resolve_max_tokens(cfg.max_tokens, args.max_tokens)
        lc_llm = get_llm(config_path=str(resolved_config_path), strict=True, max_tokens=max_tokens)
        tool = RequirementExcavationSkill(llm=lc_llm, config_path=str(resolved_config_path))

    if not args.no_openai_key_map:
        key = os.getenv(cfg.api_key_env)
        if key and not os.getenv("OPENAI_API_KEY"):
            os.environ["OPENAI_API_KEY"] = key

    if not args.skip_connectivity_test:
        try:
            with _step("进行 LLM 连通性测试（GET /models）", heartbeat="等待 LLM 连通性测试返回（网络/代理/DNS 可能较慢）"):
                base_url = (cfg.base_url or "").strip()
                if not base_url:
                    raise RuntimeError("当前配置未设置 base_url，无法进行 /models 测试")

                timeout_s = float(args.connectivity_timeout_s) if args.connectivity_timeout_s and args.connectivity_timeout_s > 0 else 20.0
                timeout_s = min(timeout_s, _timeout_seconds())
                url = base_url.rstrip("/") + "/models"
                r = httpx.get(
                    url,
                    headers={"Authorization": "Bearer " + (os.getenv("OPENAI_API_KEY") or "")},
                    timeout=httpx.Timeout(timeout_s, connect=min(10.0, timeout_s)),
                )
                if r.status_code >= 400:
                    raise RuntimeError(f"GET {url} 返回 {r.status_code}：{redact_secrets((r.text or '')[:200])}")
        except Exception as e:
            base_url = (cfg.base_url or "").strip()
            request_url = (base_url.rstrip("/") + "/chat/completions") if base_url else "(openai 默认)"
            key_present = bool(os.getenv("OPENAI_API_KEY"))
            proxy_present = any(
                bool(os.getenv(k))
                for k in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")
            )
            raise RuntimeError(
                "LLM 连通性测试失败：\n"
                f"- provider: {cfg.provider}\n"
                f"- model: {cfg.model}\n"
                f"- base_url: {cfg.base_url}\n"
                f"- request_url: {request_url}\n"
                f"- api_key_env: {cfg.api_key_env}（映射到 OPENAI_API_KEY）\n"
                f"- OPENAI_API_KEY 已设置: {key_present}\n"
                f"- 代理环境变量存在: {proxy_present}\n"
                f"- config_path: {resolved_config_path}\n"
                f"- env_path: {resolved_env_path}\n"
                "\n"
                "常见原因：base_url 写错/多了引号或反引号、网络/代理拦截、API Key 未生效。\n"
                "建议：先运行 `reqx --config path/to/llm.yaml --doctor` 确认 key 是否在进程环境里。\n"
                f"原始异常：{redact_secrets(str(e))}"
            ) from e

    surface = args.surface or "Users say onboarding is confusing"
    mode = (args.mode or os.getenv("RUN_AGENT_MODE") or "direct").strip().lower()
    if mode in {"direct", "tool", "skill"}:
        with _step("生成需求 YAML", heartbeat="正在调用 LLM 生成结果"):
            print(_tool_run(tool, surface), flush=True)
        return

    with _step("构建 CrewAI LLM"):
        timeout_s = _timeout_seconds()
        crewai_llm = LLM(
            model=cfg.model,
            provider="openai",
            temperature=cfg.temperature,
            base_url=cfg.base_url,
            api_key=os.getenv("OPENAI_API_KEY"),
            timeout=timeout_s,
            max_retries=2,
        )

    with _step("创建 Agent 与 Task"):
        agent = Agent(
            role="Requirement Structurer",
            goal="Produce structured specs",
            backstory="Uses requirement excavation skill",
            tools=[tool],
            llm=crewai_llm,
            verbose=True,
        )

        task = Task(
            description="User problem: Users say onboarding is confusing",
            agent=agent,
            expected_output="YAML spec matching README Output Contract (root_goal, proposed_solutions, selected_solution, constraints, verification_criteria, next_agents)",
        )

    crew = Crew(agents=[agent], tasks=[task])
    with _step("执行任务（可能需要较长时间）", heartbeat="正在调用 LLM 生成结果"):
        print(crew.kickoff(), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
