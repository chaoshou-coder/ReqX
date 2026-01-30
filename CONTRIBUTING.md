# Contributing

## 目录

- [开发环境](#开发环境)
- [安装（可编辑模式）](#安装可编辑模式)
- [运行测试](#运行测试)
- [提交前检查](#提交前检查)

## 开发环境

- Python 3.10+

## 安装（可编辑模式）

```bash
python -m pip install -e ".[dev]"
```

## 运行测试

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

## 提交前检查

- 确认没有提交任何密钥、Token、.env、llm.yaml 等本地配置文件
