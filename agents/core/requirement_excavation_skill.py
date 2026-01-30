from crewai.tools import BaseTool
import json
import os
from typing import Any
import yaml

from .llm_factory import get_llm, load_llm_config, redact_secrets, redact_secrets_in_obj


_PROMPT_VERSION = "2026-01-30"


class RequirementExcavationSkill(BaseTool):
    name: str = "Requirement Excavation Skill"
    description: str = (
        "Extract root goal, solution options, risks and constraints "
        "from a vague project problem and return structured YAML."
    )

    llm: Any | None = None
    config_path: str | None = None
    input_char_limit: int | None = None
    output_char_limit: int | None = None

    def _limits(self) -> tuple[int, int]:
        cached = getattr(self, "_cached_limits", None)
        if isinstance(cached, tuple) and len(cached) == 2:
            return int(cached[0]), int(cached[1])

        input_limit = self.input_char_limit
        output_limit = self.output_char_limit
        if input_limit is not None and output_limit is not None:
            limits = (int(input_limit), int(output_limit))
            setattr(self, "_cached_limits", limits)
            return limits

        cfg = load_llm_config(self.config_path, strict=True)
        if input_limit is None:
            input_limit = int(cfg.input_char_limit)
        if output_limit is None:
            output_limit = int(cfg.output_char_limit)
        limits = (int(input_limit), int(output_limit))
        setattr(self, "_cached_limits", limits)
        return limits

    def _error_yaml(self, *, code: str, message: str, details: dict[str, Any] | None = None) -> str:
        payload: dict[str, Any] = {"error": {"code": code, "message": message}}
        if details:
            payload["error"]["details"] = redact_secrets_in_obj(details)
        return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)

    def _truncate(self, text: str, limit: int) -> tuple[str, bool]:
        if limit <= 0:
            return text, False
        if len(text) <= limit:
            return text, False
        return text[:limit], True

    def _normalize_and_validate(self, data: Any) -> tuple[dict[str, Any] | None, list[str]]:
        if not isinstance(data, dict):
            return None, ["根对象必须是一个 JSON object"]

        required_str = ["surface_problem", "root_goal"]
        required_list_str = ["technical_constraints", "verification_criteria", "next_agents"]

        errors: list[str] = []
        normalized: dict[str, Any] = dict(data)

        schema_version = normalized.get("schema_version", 1)
        if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
            errors.append("schema_version 必须是正整数")
            schema_version = 1
        normalized["schema_version"] = int(schema_version)

        demand_id = normalized.get("demand_id", "auto_generated")
        if not isinstance(demand_id, str) or not demand_id.strip():
            errors.append("demand_id 必须是非空字符串")
        normalized["demand_id"] = demand_id

        normalized["expected_output_format"] = "yaml"

        for k in required_str:
            v = normalized.get(k)
            if not isinstance(v, str) or not v.strip():
                errors.append(f"{k} 必须是非空字符串")

        for k in required_list_str:
            v = normalized.get(k)
            if not isinstance(v, list) or any((not isinstance(item, str)) or (not item.strip()) for item in v):
                errors.append(f"{k} 必须是字符串数组")

        ps = normalized.get("proposed_solutions")
        if not isinstance(ps, list):
            errors.append("proposed_solutions 必须是数组")
        else:
            for i, item in enumerate(ps):
                if not isinstance(item, dict):
                    errors.append(f"proposed_solutions[{i}] 必须是 object")
                    continue
                desc = item.get("description")
                if not isinstance(desc, str) or not desc.strip():
                    errors.append(f"proposed_solutions[{i}].description 必须是非空字符串")
                for lk in ["pros", "cons"]:
                    lv = item.get(lk)
                    if not isinstance(lv, list) or any(not isinstance(x, str) for x in lv):
                        errors.append(f"proposed_solutions[{i}].{lk} 必须是字符串数组")
                risks = item.get("optimization_risks")
                if not isinstance(risks, list):
                    errors.append(f"proposed_solutions[{i}].optimization_risks 必须是数组")
                else:
                    for j, r in enumerate(risks):
                        if not isinstance(r, dict):
                            errors.append(f"proposed_solutions[{i}].optimization_risks[{j}] 必须是 object")
                            continue
                        desc = r.get("desc")
                        if not isinstance(desc, str) or not desc.strip():
                            errors.append(f"proposed_solutions[{i}].optimization_risks[{j}].desc 必须是非空字符串")
                        mitigation = r.get("mitigation")
                        if not isinstance(mitigation, str) or not mitigation.strip():
                            errors.append(f"proposed_solutions[{i}].optimization_risks[{j}].mitigation 必须是非空字符串")

        selected_solution = normalized.get("selected_solution")
        if isinstance(selected_solution, str):
            if selected_solution.strip():
                normalized["selected_solution"] = {"name": selected_solution.strip(), "reason": "未提供（兼容旧格式）"}
            else:
                errors.append("selected_solution 必须是 object")
        elif isinstance(selected_solution, dict):
            name = selected_solution.get("name")
            reason = selected_solution.get("reason")
            if not isinstance(name, str) or not name.strip():
                errors.append("selected_solution.name 必须是非空字符串")
            if not isinstance(reason, str) or not reason.strip():
                errors.append("selected_solution.reason 必须是非空字符串")
            normalized["selected_solution"] = {"name": (name or "").strip(), "reason": (reason or "").strip()}
        else:
            errors.append("selected_solution 必须是 object")

        return (normalized if not errors else None), errors

    def _run(self, surface_problem: str) -> str:
        try:
            llm = self.llm or get_llm(config_path=self.config_path, strict=True)
        except Exception as e:
            return self._error_yaml(code="llm_config_error", message="LLM 配置或初始化失败", details={"exception": redact_secrets(str(e))})
        try:
            input_limit, output_limit = self._limits()
        except Exception as e:
            return self._error_yaml(code="config_parse_error", message="配置解析失败", details={"exception": redact_secrets(str(e))})
        surface_problem_trimmed, surface_problem_truncated = self._truncate(surface_problem or "", input_limit)

        prompt = (
            "You are a requirement excavation engine.\n"
            "Return ONLY valid minified JSON. No markdown. No code fences.\n\n"
            f"Surface problem:\n{surface_problem_trimmed}\n\n"
            "JSON schema (keys must exist; use empty arrays when needed):\n"
            "{"
            '"schema_version":1,'
            '"demand_id":"auto_generated",'
            f'"prompt_version":"{_PROMPT_VERSION}",'
            '"surface_problem":"...",'
            '"root_goal":"...",'
            '"proposed_solutions":[{"description":"...","pros":[],"cons":[],"optimization_risks":[{"desc":"...","mitigation":"..."}]}],'
            '"selected_solution":{"name":"...","reason":"..."},'
            '"technical_constraints":[],'
            '"verification_criteria":[],'
            '"expected_output_format":"yaml",'
            '"next_agents":[]'
            "}\n"
        )

        try:
            raw = llm.invoke(prompt).content
        except Exception as e:
            return self._error_yaml(code="llm_invoke_failed", message="LLM 调用失败", details={"exception": redact_secrets(str(e))})

        raw_text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
        include_raw = (os.getenv("REQX_DEBUG_RAW_OUTPUT") or "").strip().lower() in {"1", "true", "yes", "y", "on"}
        preview_limit = min(int(output_limit), 2000)
        raw_preview, raw_truncated = self._truncate(raw_text, preview_limit)
        raw_preview = redact_secrets(raw_preview)

        try:
            data = json.loads(raw_text)
        except Exception as e:
            details: dict[str, Any] = {"parse_error": redact_secrets(str(e)), "raw_output_len": len(raw_text)}
            if include_raw:
                details["raw_output_preview"] = raw_preview
                details["raw_output_truncated"] = raw_truncated
            if surface_problem_truncated:
                details["surface_problem_truncated"] = True
            return self._error_yaml(code="invalid_json", message="模型输出不是合法 JSON", details=details)

        normalized, errors = self._normalize_and_validate(data)
        if normalized is None:
            details = {"schema_errors": errors, "raw_output_len": len(raw_text)}
            if include_raw:
                details["raw_output_preview"] = raw_preview
                details["raw_output_truncated"] = raw_truncated
            if surface_problem_truncated:
                details["surface_problem_truncated"] = True
            return self._error_yaml(code="invalid_schema", message="模型输出不符合约定结构", details=details)

        normalized["surface_problem"] = surface_problem_trimmed
        normalized["prompt_version"] = _PROMPT_VERSION

        result_yaml = yaml.safe_dump(normalized, sort_keys=False, allow_unicode=True)
        return result_yaml
